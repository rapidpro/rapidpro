from __future__ import unicode_literals

import json

from context_processors import GroupPermWrapper
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.http import HttpRequest
from django.test.utils import override_settings
from django.utils import timezone
from mock import patch, Mock
from smartmin.tests import SmartminTest
from temba.airtime.models import AirtimeTransfer
from temba.api.models import APIToken, Resthook
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactGroup, TEL_SCHEME, TWITTER_SCHEME
from temba.flows.models import Flow, ActionSet
from temba.locations.models import AdminBoundary
from temba.middleware import BrandingMiddleware
from temba.msgs.models import Label, Msg, INCOMING
from temba.nexmo import NexmoValidationError
from temba.orgs.models import UserSettings
from temba.tests import TembaTest, MockResponse, MockTwilioClient, MockRequestValidator, FlowFileTest
from temba.triggers.models import Trigger
from temba.utils.email import link_components
from temba.utils import languages, dict_to_struct
from .models import Org, OrgEvent, TopUp, Invitation, Language, DAYFIRST, MONTHFIRST, CURRENT_EXPORT_VERSION
from .models import CreditAlert, ORG_CREDIT_OVER, ORG_CREDIT_LOW, ORG_CREDIT_EXPIRING
from .models import UNREAD_FLOW_MSGS, UNREAD_INBOX_MSGS, TopUpCredits
from .models import WHITELISTED, SUSPENDED, RESTORED
from .tasks import squash_topupcredits


class OrgContextProcessorTest(TembaTest):

    def test_group_perms_wrapper(self):
        administrators = Group.objects.get(name="Administrators")
        editors = Group.objects.get(name="Editors")
        viewers = Group.objects.get(name="Viewers")

        administrators_wrapper = GroupPermWrapper(administrators)
        self.assertTrue(administrators_wrapper['msgs']['msg_api'])
        self.assertTrue(administrators_wrapper["msgs"]["msg_inbox"])

        editors_wrapper = GroupPermWrapper(editors)
        self.assertFalse(editors_wrapper["msgs"]["org_plan"])
        self.assertTrue(editors_wrapper["msgs"]["msg_inbox"])

        viewers_wrapper = GroupPermWrapper(viewers)
        self.assertFalse(viewers_wrapper["msgs"]["msg_api"])
        self.assertTrue(viewers_wrapper["msgs"]["msg_inbox"])


class OrgTest(TembaTest):

    def test_get_org_users(self):
        org_users = self.org.get_org_users()
        self.assertTrue(self.user in org_users)
        self.assertTrue(self.surveyor in org_users)
        self.assertTrue(self.editor in org_users)
        self.assertTrue(self.admin in org_users)

        # should be ordered by email
        self.assertEqual(self.admin, org_users[0])
        self.assertEqual(self.editor, org_users[1])
        self.assertEqual(self.surveyor, org_users[2])
        self.assertEqual(self.user, org_users[3])

    def test_get_unique_slug(self):
        self.org.slug = 'allo'
        self.org.save()

        self.assertEqual(Org.get_unique_slug('foo'), 'foo')
        self.assertEqual(Org.get_unique_slug('Which part?'), 'which-part')
        self.assertEqual(Org.get_unique_slug('Allo'), 'allo-2')

    def test_languages(self):
        self.assertEqual(self.org.get_language_codes(), set())

        self.org.set_languages(self.admin, ['eng', 'fre'], 'eng')
        self.org.refresh_from_db()

        self.assertEqual({l.name for l in self.org.languages.all()}, {"English", "French"})
        self.assertEqual(self.org.primary_language.name, "English")
        self.assertEqual(self.org.get_language_codes(), {'eng', 'fre'})

        self.org.set_languages(self.admin, ['eng', 'kin'], 'kin')
        self.org.refresh_from_db()

        self.assertEqual({l.name for l in self.org.languages.all()}, {"English", "Kinyarwanda"})
        self.assertEqual(self.org.primary_language.name, "Kinyarwanda")
        self.assertEqual(self.org.get_language_codes(), {'eng', 'kin'})

    def test_get_channel_countries(self):
        self.assertEqual(self.org.get_channel_countries(), [])

        self.org.connect_transferto('mylogin', 'api_token', self.admin)

        self.assertEqual(self.org.get_channel_countries(), [dict(code='RW', name='Rwanda', currency_name='Rwanda Franc',
                                                                 currency_code='RWF')])

        Channel.create(self.org, self.user, 'US', 'A', None, "+12001112222", gcm_id="asdf", secret="asdf")

        self.assertEqual(self.org.get_channel_countries(), [dict(code='RW', name='Rwanda', currency_name='Rwanda Franc',
                                                                 currency_code='RWF'),
                                                            dict(code='US', name='United States',
                                                                 currency_name='US Dollar', currency_code='USD')])

        Channel.create(self.org, self.user, None, 'TT', name="Twitter Channel",
                       address="billy_bob", role="SR", scheme='twitter')

        self.assertEqual(self.org.get_channel_countries(), [dict(code='RW', name='Rwanda', currency_name='Rwanda Franc',
                                                                 currency_code='RWF'),
                                                            dict(code='US', name='United States',
                                                                 currency_name='US Dollar', currency_code='USD')])

    def test_edit(self):
        # use a manager now
        self.login(self.admin)

        # can we see the edit page
        response = self.client.get(reverse('orgs.org_edit'))
        self.assertEquals(200, response.status_code)

        # update the name and slug of the organization
        data = dict(name="Temba", timezone="Africa/Kigali", date_format=DAYFIRST, slug="nice temba")
        response = self.client.post(reverse('orgs.org_edit'), data)
        self.assertTrue('slug' in response.context['form'].errors)

        data = dict(name="Temba", timezone="Africa/Kigali", date_format=MONTHFIRST, slug="nice-temba")
        response = self.client.post(reverse('orgs.org_edit'), data)
        self.assertEquals(302, response.status_code)

        org = Org.objects.get(pk=self.org.pk)
        self.assertEquals("Temba", org.name)
        self.assertEquals("nice-temba", org.slug)

    def test_recommended_channel(self):
        self.org.timezone = 'Africa/Nairobi'
        self.org.save()
        self.assertEquals(self.org.get_recommended_channel(), 'africastalking')

        self.org.timezone = 'America/Phoenix'
        self.org.save()
        self.assertEquals(self.org.get_recommended_channel(), 'twilio')

        self.org.timezone = 'Asia/Jakarta'
        self.org.save()
        self.assertEquals(self.org.get_recommended_channel(), 'hub9')

        self.org.timezone = 'Africa/Mogadishu'
        self.org.save()
        self.assertEquals(self.org.get_recommended_channel(), 'shaqodoon')

        self.org.timezone = 'Europe/Amsterdam'
        self.org.save()
        self.assertEquals(self.org.get_recommended_channel(), 'nexmo')

        self.org.timezone = 'Africa/Kigali'
        self.org.save()
        self.assertEquals(self.org.get_recommended_channel(), 'android')

    def test_country(self):
        country_url = reverse('orgs.org_country')

        # can't see this page if not logged in
        self.assertLoginRedirect(self.client.get(country_url))

        # login as admin instead
        self.login(self.admin)
        response = self.client.get(country_url)
        self.assertEquals(200, response.status_code)

        # save with Rwanda as a country
        response = self.client.post(country_url, dict(country=AdminBoundary.objects.get(name='Rwanda').pk))

        # assert it has changed
        org = Org.objects.get(pk=self.org.pk)
        self.assertEqual("Rwanda", unicode(org.country))
        self.assertEqual("RW", org.get_country_code())

        # set our admin boundary name to something invalid
        org.country.name = 'Fantasia'
        org.country.save()

        # getting our country code show now back down to our channel
        self.assertEqual('RW', org.get_country_code())

        # clear it out
        self.client.post(country_url, dict(country=''))

        # assert it has been
        org = Org.objects.get(pk=self.org.pk)
        self.assertFalse(org.country)
        self.assertEquals('RW', org.get_country_code())

        # remove all our channels so we no longer have a backdown
        org.channels.all().delete()
        org = Org.objects.get(pk=self.org.pk)

        # now really don't have a clue of our country code
        self.assertIsNone(org.get_country_code())

    def test_plans(self):
        self.contact = self.create_contact("Joe", "+250788123123")

        self.create_msg(direction=INCOMING, contact=self.contact, text="Orange")

        # check start and end date for this plan
        self.assertEquals(timezone.now().date(), self.org.current_plan_start())
        self.assertEquals(timezone.now().date() + relativedelta(months=1), self.org.current_plan_end())

        # check our credits
        self.login(self.admin)
        response = self.client.get(reverse('orgs.org_home'))
        self.assertContains(response, "999")

        # view our topups
        response = self.client.get(reverse('orgs.topup_list'))

        # should say we have a 1,000 credits too
        self.assertContains(response, "999")

        # and that we have 999 credits left on our topup
        self.assertContains(response, "1 of 1,000 Credits Used")

        # our receipt should show that the topup was free
        with patch('stripe.Charge.retrieve') as stripe:
            stripe.return_value = ''
            response = self.client.get(reverse('orgs.topup_read', args=[TopUp.objects.filter(org=self.org).first().pk]))
            self.assertContains(response, '1000 Credits')

    def test_user_update(self):
        update_url = reverse('orgs.user_edit')
        login_url = reverse('users.user_login')

        # no access if anonymous
        response = self.client.get(update_url)
        self.assertRedirect(response, login_url)

        self.login(self.admin)

        # change the user language
        post_data = dict(language='pt-br', first_name='Admin', last_name='User', email='administrator@temba.com', current_password='Administrator')
        response = self.client.post(update_url, post_data)
        self.assertRedirect(response, reverse('orgs.org_home'))

        # check that our user settings have changed
        settings = self.admin.get_settings()
        self.assertEquals('pt-br', settings.language)

    def test_usersettings(self):
        self.login(self.admin)

        post_data = dict(tel='+250788382382')
        self.client.post(reverse('orgs.usersettings_phone'), post_data)
        self.assertEquals('+250 788 382 382', UserSettings.objects.get(user=self.admin).get_tel_formatted())

        post_data = dict(tel='bad number')
        response = self.client.post(reverse('orgs.usersettings_phone'), post_data)
        self.assertEquals(response.context['form'].errors['tel'][0], 'Invalid phone number, try again.')

    def test_org_suspension(self):
        from temba.flows.models import FlowRun

        self.login(self.admin)
        self.org.set_suspended()
        self.org.refresh_from_db()

        self.assertEqual(True, self.org.is_suspended())

        self.assertEqual(0, Msg.all_messages.all().count())
        self.assertEqual(0, FlowRun.objects.all().count())

        # while we are suspended, we can't send broadcasts
        send_url = reverse('msgs.broadcast_send')
        mark = self.create_contact('Mark', number='+12065551212')
        post_data = dict(text="send me ur bank account login im ur friend.", omnibox="c-%s" % mark.uuid)
        response = self.client.post(send_url, post_data, follow=True)

        self.assertEquals('Sorry, your account is currently suspended. To enable sending messages, please contact support.',
                          response.context['form'].errors['__all__'][0])

        # we also can't start flows
        flow = self.create_flow()
        post_data = dict(omnibox="c-%s" % mark.uuid, restart_participants='on')
        response = self.client.post(reverse('flows.flow_broadcast', args=[flow.pk]), post_data, follow=True)

        self.assertEquals('Sorry, your account is currently suspended. To enable sending messages, please contact support.',
                          response.context['form'].errors['__all__'][0])

        # or use the api to do either
        def postAPI(url, data):
            response = self.client.post(url + ".json", json.dumps(data), content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
            if response.content:
                response.json = json.loads(response.content)
            return response

        url = reverse('api.v1.broadcasts')
        response = postAPI(url, dict(contacts=[mark.uuid], text="You are adistant cousin to a wealthy person."))
        self.assertContains(response, "Sorry, your account is currently suspended. To enable sending messages, please contact support.", status_code=400)

        url = reverse('api.v1.runs')
        response = postAPI(url, dict(flow_uuid=flow.uuid, phone="+250788123123"))
        self.assertContains(response, "Sorry, your account is currently suspended. To enable sending messages, please contact support.", status_code=400)

        # still no messages or runs
        self.assertEqual(0, Msg.all_messages.all().count())
        self.assertEqual(0, FlowRun.objects.all().count())

        # unsuspend our org and start a flow
        self.org.set_restored()
        post_data = dict(omnibox="c-%s" % mark.uuid, restart_participants='on')
        response = self.client.post(reverse('flows.flow_broadcast', args=[flow.pk]), post_data, follow=True)
        self.assertEqual(1, FlowRun.objects.all().count())

    def test_webhook_headers(self):
        update_url = reverse('orgs.org_webhook')
        login_url = reverse('users.user_login')

        # no access if anonymous
        response = self.client.get(update_url)
        self.assertRedirect(response, login_url)

        self.login(self.admin)

        response = self.client.get(update_url)
        self.assertEquals(200, response.status_code)

        # set a webhook with headers
        post_data = response.context['form'].initial
        post_data['webhook'] = 'http://webhooks.uniceflabs.org'
        post_data['header_1_key'] = 'Authorization'
        post_data['header_1_value'] = 'Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=='

        response = self.client.post(update_url, post_data)
        self.assertEquals(302, response.status_code)
        self.assertRedirect(response, reverse('orgs.org_home'))

        # check that our webhook settings have changed
        org = Org.objects.get(pk=self.org.pk)
        self.assertEquals('http://webhooks.uniceflabs.org', org.get_webhook_url())
        self.assertDictEqual({'Authorization': 'Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=='}, org.get_webhook_headers())

    def test_org_administration(self):
        manage_url = reverse('orgs.org_manage')
        update_url = reverse('orgs.org_update', args=[self.org.pk])
        login_url = reverse('users.user_login')

        # no access to anon
        response = self.client.get(manage_url)
        self.assertRedirect(response, login_url)

        response = self.client.get(update_url)
        self.assertRedirect(response, login_url)

        # or admins
        self.login(self.admin)

        response = self.client.get(manage_url)
        self.assertRedirect(response, login_url)

        response = self.client.get(update_url)
        self.assertRedirect(response, login_url)

        # only superuser
        self.login(self.superuser)

        response = self.client.get(manage_url)
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "(Suspended)")

        self.org.set_suspended()
        response = self.client.get(manage_url)
        self.assertContains(response, "(Suspended)")

        # should contain our test org
        self.assertContains(response, "Temba")

        # and can go to that org
        response = self.client.get(update_url)
        self.assertEquals(200, response.status_code)

        post_data = response.context['form'].initial
        post_data['plan'] = 'TRIAL'
        post_data['language'] = ''
        post_data['country'] = ''
        post_data['primary_language'] = ''
        post_data['parent'] = ''

        # change to the trial plan
        response = self.client.post(update_url, post_data)
        self.assertEquals(302, response.status_code)

        # restore
        post_data['status'] = RESTORED
        response = self.client.post(update_url, post_data)
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_suspended())

        # white list
        post_data['status'] = WHITELISTED
        response = self.client.post(update_url, post_data)
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_whitelisted())

        # suspend
        post_data['status'] = SUSPENDED
        response = self.client.post(update_url, post_data)
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_suspended())

    def test_accounts(self):
        url = reverse('orgs.org_accounts')
        self.login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'If you use the RapidPro Surveyor application to run flows offline')

        Org.objects.create(name="Another Org", timezone="Africa/Kigali", country=self.country,
                           brand='rapidpro.io', created_by=self.user, modified_by=self.user,
                           surveyor_password='nyaruka')

        response = self.client.post(url, dict(surveyor_password='nyaruka'))
        self.org.refresh_from_db()
        self.assertContains(response, 'This password is not valid. Choose a new password and try again.')
        self.assertIsNone(self.org.surveyor_password)

        # now try again, but with a unique password
        response = self.client.post(url, dict(surveyor_password='unique password'))
        self.org.refresh_from_db()
        self.assertEqual('unique password', self.org.surveyor_password)

    def test_refresh_tokens(self):
        self.login(self.admin)
        url = reverse('orgs.org_home')
        response = self.client.get(url)

        # admin should have a token
        token = APIToken.objects.get(user=self.admin)

        # and it should be on the page
        self.assertContains(response, token.key)

        # let's refresh it
        self.client.post(reverse('api.apitoken_refresh'))

        # visit our account page again
        response = self.client.get(url)

        # old token no longer there
        self.assertNotContains(response, token.key)

        # old token now inactive
        token.refresh_from_db()
        self.assertFalse(token.is_active)

        # there is a new token for this user
        new_token = APIToken.objects.get(user=self.admin, is_active=True)
        self.assertNotEqual(new_token.key, token.key)
        self.assertContains(response, new_token.key)

        # can't refresh if logged in as viewer
        self.login(self.user)
        response = self.client.post(reverse('api.apitoken_refresh'))
        self.assertLoginRedirect(response)

        # or just not an org user
        self.login(self.non_org_user)
        response = self.client.post(reverse('api.apitoken_refresh'))
        self.assertLoginRedirect(response)

    @override_settings(SEND_EMAILS=True)
    def test_manage_accounts(self):
        url = reverse('orgs.org_manage_accounts')

        self.login(self.admin)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # give users an API token and give admin and editor an additional surveyor-role token
        APIToken.get_or_create(self.org, self.admin)
        APIToken.get_or_create(self.org, self.editor)
        APIToken.get_or_create(self.org, self.surveyor)
        APIToken.get_or_create(self.org, self.admin, role=Group.objects.get(name="Surveyors"))
        APIToken.get_or_create(self.org, self.editor, role=Group.objects.get(name="Surveyors"))

        # we have 19 fields in the form including 16 checkboxes for the four users, an email field, a user group field
        # and 'loc' field.
        expected_fields = {'invite_emails', 'invite_group', 'loc'}
        for user in (self.surveyor, self.user, self.editor, self.admin):
            for group in ('administrators', 'editors', 'viewers', 'surveyors'):
                expected_fields.add(group + '_%d' % user.pk)

        self.assertEqual(set(response.context['form'].fields.keys()), expected_fields)
        self.assertEqual(response.context['form'].initial, {
            'administrators_%d' % self.admin.pk: True,
            'editors_%d' % self.editor.pk: True,
            'viewers_%d' % self.user.pk: True,
            'surveyors_%d' % self.surveyor.pk: True
        })
        self.assertEqual(response.context['form'].fields['invite_emails'].initial, None)
        self.assertEqual(response.context['form'].fields['invite_group'].initial, 'V')

        # keep admin as admin, editor as editor, but make user an editor too, and remove surveyor
        post_data = {
            'administrators_%d' % self.admin.pk: 'on',
            'editors_%d' % self.editor.pk: 'on',
            'editors_%d' % self.user.pk: 'on',
            'invite_emails': "",
            'invite_group': "V"
        }
        response = self.client.post(url, post_data)
        self.assertRedirect(response, reverse('orgs.org_manage_accounts'))

        self.org.refresh_from_db()
        self.assertEqual(set(self.org.administrators.all()), {self.admin})
        self.assertEqual(set(self.org.editors.all()), {self.user, self.editor})
        self.assertFalse(set(self.org.viewers.all()), set())
        self.assertEqual(set(self.org.surveyors.all()), set())

        # our surveyor's API token will have been deleted
        self.assertEqual(self.admin.api_tokens.filter(is_active=True).count(), 2)
        self.assertEqual(self.editor.api_tokens.filter(is_active=True).count(), 2)
        self.assertEqual(self.surveyor.api_tokens.filter(is_active=True).count(), 0)

        # next we leave existing roles unchanged, but try to invite new user to be admin with invalid email address
        post_data['invite_emails'] = "norkans7gmail.com"
        post_data['invite_group'] = 'A'
        response = self.client.post(url, post_data)

        self.assertFormError(response, 'form', 'invite_emails', "One of the emails you entered is invalid.")

        # try again with valid email
        post_data['invite_emails'] = "norkans7@gmail.com"
        response = self.client.post(url, post_data)
        self.assertRedirect(response, reverse('orgs.org_manage_accounts'))

        # an invitation is created
        invitation = Invitation.objects.get()
        self.assertEqual(invitation.org, self.org)
        self.assertEqual(invitation.email, "norkans7@gmail.com")
        self.assertEqual(invitation.user_group, "A")

        # and sent by email
        self.assertTrue(len(mail.outbox) == 1)

        # pretend our invite was acted on
        invitation.is_active = False
        invitation.save()

        # send another invitation, different group
        post_data['invite_emails'] = "norkans7@gmail.com"
        post_data['invite_group'] = 'E'
        self.client.post(url, post_data)

        # old invite should be updated
        invitation.refresh_from_db()
        self.assertEqual(invitation.user_group, 'E')
        self.assertTrue(invitation.is_active)

        # and new email sent
        self.assertEqual(len(mail.outbox), 2)

        # include multiple emails on the form
        post_data['invite_emails'] = "norbert@temba.com,code@temba.com"
        post_data['invite_group'] = 'A'
        self.client.post(url, post_data)

        # now 2 new invitations are created and sent
        self.assertEqual(Invitation.objects.all().count(), 3)
        self.assertEqual(len(mail.outbox), 4)

        response = self.client.get(url)

        # user ordered by email
        self.assertEqual(list(response.context['org_users']), [self.admin, self.editor, self.user])

        # invites ordered by email as well
        self.assertEqual(response.context['invites'][0].email, 'code@temba.com')
        self.assertEqual(response.context['invites'][1].email, 'norbert@temba.com')
        self.assertEqual(response.context['invites'][2].email, 'norkans7@gmail.com')

        # finally downgrade the editor to a surveyor and remove ourselves entirely from this org
        response = self.client.post(url, {
            'editors_%d' % self.user.pk: 'on',
            'surveyors_%d' % self.editor.pk: 'on',
            'invite_emails': "",
            'invite_group': 'V'
        })

        # we should be redirected to chooser page
        self.assertRedirect(response, reverse('orgs.org_choose'))

        # and removed from this org
        self.org.refresh_from_db()
        self.assertEqual(set(self.org.administrators.all()), set())
        self.assertEqual(set(self.org.editors.all()), {self.user})
        self.assertEqual(set(self.org.viewers.all()), set())
        self.assertEqual(set(self.org.surveyors.all()), {self.editor})

        # editor will have lost their editor API token, but not their surveyor token
        self.editor.refresh_from_db()
        self.assertEqual([t.role.name for t in self.editor.api_tokens.filter(is_active=True)], ["Surveyors"])

        # and all our API tokens for the admin are deleted
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.api_tokens.filter(is_active=True).count(), 0)

    @patch('temba.utils.email.send_temba_email')
    def test_join(self, mock_send_temba_email):

        def create_invite(group):
            return Invitation.objects.create(org=self.org,
                                             user_group=group,
                                             email="norkans7@gmail.com",
                                             created_by=self.admin,
                                             modified_by=self.admin)

        editor_invitation = create_invite('E')
        editor_invitation.send_invitation()
        email_args = mock_send_temba_email.call_args[0]  # all positional args

        self.assertEqual(email_args[0], "RapidPro Invitation")
        self.assertIn('https://app.rapidpro.io/org/join/%s/' % editor_invitation.secret, email_args[1])
        self.assertNotIn('{{', email_args[1])
        self.assertIn('https://app.rapidpro.io/org/join/%s/' % editor_invitation.secret, email_args[2])
        self.assertNotIn('{{', email_args[2])

        editor_join_url = reverse('orgs.org_join', args=[editor_invitation.secret])
        self.client.logout()

        # if no user is logged we redirect to the create_login page
        response = self.client.get(editor_join_url)
        self.assertEqual(302, response.status_code)
        response = self.client.get(editor_join_url, follow=True)
        self.assertEqual(response.request['PATH_INFO'], reverse('orgs.org_create_login', args=[editor_invitation.secret]))

        # a user is already logged in
        self.invited_editor = self.create_user("InvitedEditor")
        self.login(self.invited_editor)

        response = self.client.get(editor_join_url)
        self.assertEqual(200, response.status_code)

        self.assertEqual(self.org.pk, response.context['org'].pk)
        # we have a form without field except one 'loc'
        self.assertEqual(1, len(response.context['form'].fields))

        post_data = dict()
        response = self.client.post(editor_join_url, post_data, follow=True)
        self.assertEqual(200, response.status_code)

        self.assertIn(self.invited_editor, self.org.editors.all())
        self.assertFalse(Invitation.objects.get(pk=editor_invitation.pk).is_active)

        roles = (('V', self.org.viewers), ('S', self.org.surveyors),
                 ('A', self.org.administrators), ('E', self.org.editors))

        # test it for each role
        for role in roles:
            invite = create_invite(role[0])
            user = self.create_user('User%s' % role[0])
            self.login(user)
            response = self.client.post(reverse('orgs.org_join', args=[invite.secret]), follow=True)
            self.assertEqual(200, response.status_code)
            self.assertIsNotNone(role[1].filter(pk=user.pk).first())

        # try an expired invite
        invite = create_invite('S')
        invite.is_active = False
        invite.save()
        expired_user = self.create_user("InvitedExpired")
        self.login(expired_user)
        response = self.client.post(reverse('orgs.org_join', args=[invite.secret]), follow=True)
        self.assertEqual(200, response.status_code)
        self.assertIsNone(self.org.surveyors.filter(pk=expired_user.pk).first())

    def test_create_login(self):
        admin_invitation = Invitation.objects.create(org=self.org,
                                                     user_group="A",
                                                     email="norkans7@gmail.com",
                                                     created_by=self.admin,
                                                     modified_by=self.admin)

        admin_create_login_url = reverse('orgs.org_create_login', args=[admin_invitation.secret])
        self.client.logout()

        response = self.client.get(admin_create_login_url)
        self.assertEquals(200, response.status_code)

        self.assertEquals(self.org.pk, response.context['org'].pk)

        # we have a form with 4 fields and one hidden 'loc'
        self.assertEquals(5, len(response.context['form'].fields))
        self.assertTrue('first_name' in response.context['form'].fields)
        self.assertTrue('last_name' in response.context['form'].fields)
        self.assertTrue('email' in response.context['form'].fields)
        self.assertTrue('password' in response.context['form'].fields)

        post_data = dict()
        post_data['first_name'] = "Norbert"
        post_data['last_name'] = "Kwizera"
        post_data['email'] = "norkans7@gmail.com"
        post_data['password'] = "norbertkwizeranorbert"

        response = self.client.post(admin_create_login_url, post_data, follow=True)
        self.assertEquals(200, response.status_code)

        new_invited_user = User.objects.get(email="norkans7@gmail.com")
        self.assertTrue(new_invited_user in self.org.administrators.all())
        self.assertFalse(Invitation.objects.get(pk=admin_invitation.pk).is_active)

    def test_surveyor_invite(self):
        surveyor_invite = Invitation.objects.create(org=self.org,
                                                    user_group="S",
                                                    email="surveyor@gmail.com",
                                                    created_by=self.admin,
                                                    modified_by=self.admin)

        admin_create_login_url = reverse('orgs.org_create_login', args=[surveyor_invite.secret])
        self.client.logout()

        post_data = dict(first_name='Surveyor', last_name='User', email='surveyor@gmail.com', password='password')
        response = self.client.post(admin_create_login_url, post_data, follow=True)
        self.assertEquals(200, response.status_code)

        # as a surveyor we should have been rerourted
        self.assertEquals(reverse('orgs.org_surveyor'), response._request.path)
        self.assertFalse(Invitation.objects.get(pk=surveyor_invite.pk).is_active)

        # make sure we are a surveyor
        new_invited_user = User.objects.get(email="surveyor@gmail.com")
        self.assertTrue(new_invited_user in self.org.surveyors.all())

        # if we login, we should be rerouted too
        self.client.logout()
        response = self.client.post('/users/login/', {'username': 'surveyor@gmail.com', 'password': 'password'}, follow=True)
        self.assertEquals(200, response.status_code)
        self.assertEquals(reverse('orgs.org_surveyor'), response._request.path)

    def test_surveyor(self):
        self.client.logout()
        url = '%s?mobile=true' % reverse('orgs.org_surveyor')

        # try creating a surveyor account with a bogus password
        post_data = dict(surveyor_password='badpassword')
        response = self.client.post(url, post_data)
        self.assertContains(response, 'Invalid surveyor password, please check with your project leader and try again.')

        # save a surveyor password
        self.org.surveyor_password = 'nyaruka'
        self.org.save()

        # now lets try again
        post_data = dict(surveyor_password='nyaruka')
        response = self.client.post(url, post_data)
        self.assertContains(response, 'Enter your details below to create your account.')

        # now try creating an account on the second step without and surveyor_password
        post_data = dict(first_name='Marshawn', last_name='Lynch',
                         password='beastmode24', email='beastmode@seahawks.com')
        response = self.client.post(url, post_data)
        self.assertContains(response, 'Enter your details below to create your account.')

        # now do the same but with a valid surveyor_password
        post_data = dict(first_name='Marshawn', last_name='Lynch',
                         password='beastmode24', email='beastmode@seahawks.com',
                         surveyor_password='nyaruka')
        response = self.client.post(url, post_data)
        self.assertTrue('token' in response.url)
        self.assertTrue('beastmode' in response.url)
        self.assertTrue('Temba' in response.url)

        # try with a login that already exists
        post_data = dict(first_name='Resused', last_name='Email',
                         password='mypassword1', email='beastmode@seahawks.com',
                         surveyor_password='nyaruka')
        response = self.client.post(url, post_data)
        self.assertContains(response, 'That email address is already used')

        # try with a login that already exists
        post_data = dict(first_name='Short', last_name='Password',
                         password='short', email='thomasrawls@seahawks.com',
                         surveyor_password='nyaruka')
        response = self.client.post(url, post_data)
        self.assertContains(response, 'Passwords must contain at least 8 letters')

        # finally make sure our login works
        success = self.client.login(username='beastmode@seahawks.com', password='beastmode24')
        self.assertTrue(success)

        # and that we only have the surveyor role
        self.assertIsNotNone(self.org.surveyors.filter(username='beastmode@seahawks.com').first())
        self.assertIsNone(self.org.administrators.filter(username='beastmode@seahawks.com').first())
        self.assertIsNone(self.org.editors.filter(username='beastmode@seahawks.com').first())
        self.assertIsNone(self.org.viewers.filter(username='beastmode@seahawks.com').first())

    def test_choose(self):
        self.client.logout()

        choose_url = reverse('orgs.org_choose')

        # have a second org
        self.create_secondary_org()
        self.login(self.admin)

        response = self.client.get(reverse('orgs.org_home'))
        self.assertEquals(response.context['org'], self.org)

        # add self.manager to self.org2 viewers
        self.org2.viewers.add(self.admin)

        response = self.client.get(choose_url)
        self.assertEquals(200, response.status_code)

        self.assertTrue('organization' in response.context['form'].fields)

        post_data = dict()
        post_data['organization'] = self.org2.pk

        response = self.client.post(choose_url, post_data, follow=True)
        self.assertEquals(200, response.status_code)
        response = self.client.get(reverse('orgs.org_home'))
        self.assertEquals(response.context_data['org'], self.org2)

        # a non org user get's logged out
        self.login(self.non_org_user)
        response = self.client.get(choose_url)
        self.assertRedirect(response, reverse('users.user_login'))

        # superuser gets redirected to user management page
        self.login(self.superuser)
        response = self.client.get(choose_url, follow=True)
        self.assertContains(response, "Organizations")

    def test_topup_admin(self):
        self.login(self.admin)

        topup = TopUp.objects.get()

        # admins shouldn't be able to see the create / manage / update pages
        manage_url = reverse('orgs.topup_manage') + "?org=%d" % self.org.id
        self.assertRedirect(self.client.get(manage_url), '/users/login/')

        create_url = reverse('orgs.topup_create') + "?org=%d" % self.org.id
        self.assertRedirect(self.client.get(create_url), '/users/login/')

        update_url = reverse('orgs.topup_update', args=[topup.pk])
        self.assertRedirect(self.client.get(update_url), '/users/login/')

        # log in as root
        self.login(self.superuser)

        # should list our one topup
        response = self.client.get(manage_url)
        self.assertEquals(1, len(response.context['object_list']))

        # create a new one
        post_data = dict(price='1000', credits='500', comment="")
        response = self.client.post(create_url, post_data)
        self.assertEquals(2, TopUp.objects.filter(org=self.org).count())
        self.assertEquals(1500, self.org.get_credits_remaining())

        # update one of our topups
        post_data = dict(is_active=True, price='0', credits='5000', comment="", expires_on="2025-04-03 13:47:46")
        response = self.client.post(update_url, post_data)

        self.assertEquals(5500, self.org.get_credits_remaining())

    def test_topup_model(self):
        topup = TopUp.create(self.admin, price=None, credits=1000)

        self.assertEqual(topup.get_price_display(), "")

        topup.price = 0
        topup.save()

        self.assertEqual(topup.get_price_display(), "Free")

        topup.price = 100
        topup.save()

        self.assertEqual(topup.get_price_display(), "$1.00")

    def test_topups(self):

        settings.BRANDING[settings.DEFAULT_BRAND]['tiers'] = dict(multi_user=100000, multi_org=1000000)

        contact = self.create_contact("Michael Shumaucker", "+250788123123")
        test_contact = Contact.get_test_contact(self.user)
        welcome_topup = TopUp.objects.get()

        def create_msgs(recipient, count):
            for m in range(count):
                self.create_msg(contact=recipient, direction='I', text="Test %d" % m)

        create_msgs(contact, 10)

        with self.assertNumQueries(1):
            self.assertEquals(150, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEquals(150, self.org.get_low_credits_threshold())

        # we should have 1000 minus 10 credits for this org
        with self.assertNumQueries(4):
            self.assertEquals(990, self.org.get_credits_remaining())  # from db

        with self.assertNumQueries(0):
            self.assertEquals(1000, self.org.get_credits_total())  # from cache
            self.assertEquals(10, self.org.get_credits_used())
            self.assertEquals(990, self.org.get_credits_remaining())

        self.assertEquals(10, welcome_topup.msgs.count())
        self.assertEquals(10, TopUp.objects.get(pk=welcome_topup.pk).get_used())

        # at this point we shouldn't have squashed any topupcredits, so should have the same number as our used
        self.assertEqual(10, TopUpCredits.objects.all().count())

        # now squash
        squash_topupcredits()

        # should only have one remaining
        self.assertEqual(1, TopUpCredits.objects.all().count())

        # reduce our credits on our topup to 15
        TopUp.objects.filter(pk=welcome_topup.pk).update(credits=15)
        self.org.update_caches(OrgEvent.topup_updated, None)  # invalidates our credits remaining cache

        self.assertEquals(15, self.org.get_credits_total())
        self.assertEquals(5, self.org.get_credits_remaining())

        # create 10 more messages, only 5 of which will get a topup
        create_msgs(contact, 10)

        self.assertEquals(15, TopUp.objects.get(pk=welcome_topup.pk).msgs.count())
        self.assertEquals(15, TopUp.objects.get(pk=welcome_topup.pk).get_used())

        self.assertFalse(self.org._calculate_active_topup())

        with self.assertNumQueries(0):
            self.assertEquals(15, self.org.get_credits_total())
            self.assertEquals(20, self.org.get_credits_used())
            self.assertEquals(-5, self.org.get_credits_remaining())

        # again create 10 more messages, none of which will get a topup
        create_msgs(contact, 10)

        with self.assertNumQueries(0):
            self.assertEquals(15, self.org.get_credits_total())
            self.assertEquals(30, self.org.get_credits_used())
            self.assertEquals(-15, self.org.get_credits_remaining())

        self.assertEquals(15, TopUp.objects.get(pk=welcome_topup.pk).get_used())

        # raise our topup to take 20 and create another for 5
        TopUp.objects.filter(pk=welcome_topup.pk).update(credits=20)
        new_topup = TopUp.create(self.admin, price=0, credits=5)
        self.org.update_caches(OrgEvent.topup_updated, None)

        # apply topups which will max out both and reduce debt to 5
        self.org.apply_topups()

        self.assertEquals(20, welcome_topup.msgs.count())
        self.assertEquals(20, TopUp.objects.get(pk=welcome_topup.pk).get_used())
        self.assertEquals(5, new_topup.msgs.count())
        self.assertEquals(5, TopUp.objects.get(pk=new_topup.pk).get_used())
        self.assertEquals(25, self.org.get_credits_total())
        self.assertEquals(30, self.org.get_credits_used())
        self.assertEquals(-5, self.org.get_credits_remaining())

        # create a message from our test contact, should not count against our totals
        test_msg = self.create_msg(contact=test_contact, direction='I', text="Test")

        self.assertIsNone(test_msg.topup_id)
        self.assertEquals(30, self.org.get_credits_used())

        # test special status
        self.assertFalse(self.org.is_multi_user_tier())
        self.assertFalse(self.org.is_multi_org_tier())

        # add new topup with lots of credits
        mega_topup = TopUp.create(self.admin, price=0, credits=100000)
        self.org.update_caches(OrgEvent.topup_updated, None)

        # after applying this, no non-test messages should be without a topup
        self.org.apply_topups()
        self.assertFalse(Msg.all_messages.filter(org=self.org, contact__is_test=False, topup=None))
        self.assertFalse(Msg.all_messages.filter(org=self.org, contact__is_test=True).exclude(topup=None))
        self.assertEquals(5, TopUp.objects.get(pk=mega_topup.pk).get_used())

        # we aren't yet multi user since this topup was free
        self.assertEquals(0, self.org.get_purchased_credits())
        self.assertFalse(self.org.is_multi_user_tier())

        self.assertEquals(100025, self.org.get_credits_total())
        self.assertEquals(30, self.org.get_credits_used())
        self.assertEquals(99995, self.org.get_credits_remaining())

        # and new messages use the mega topup
        msg = self.create_msg(contact=contact, direction='I', text="Test")
        self.assertEquals(msg.topup, mega_topup)

        self.assertEquals(6, TopUp.objects.get(pk=mega_topup.pk).get_used())

        # but now it expires
        yesterday = timezone.now() - relativedelta(days=1)
        mega_topup.expires_on = yesterday
        mega_topup.save(update_fields=['expires_on'])
        self.org.update_caches(OrgEvent.topup_updated, None)

        # new incoming messages should not be assigned a topup
        msg = self.create_msg(contact=contact, direction='I', text="Test")
        self.assertIsNone(msg.topup)

        # check our totals
        self.org.update_caches(OrgEvent.topup_updated, None)

        with self.assertNumQueries(3):
            self.assertEquals(0, self.org.get_purchased_credits())
            self.assertEquals(31, self.org.get_credits_total())
            self.assertEquals(32, self.org.get_credits_used())
            self.assertEquals(-1, self.org.get_credits_remaining())

        # all top up expired
        TopUp.objects.all().update(expires_on=yesterday)

        # we have expiring credits, and no more active
        gift_topup = TopUp.create(self.admin, price=0, credits=100)
        next_week = timezone.now() + relativedelta(days=7)
        gift_topup.expires_on = next_week
        gift_topup.save(update_fields=['expires_on'])
        self.org.update_caches(OrgEvent.topup_updated, None)
        self.org.apply_topups()

        with self.assertNumQueries(3):
            self.assertEquals(99, self.org.get_credits_expiring_soon())

        with self.assertNumQueries(1):
            self.assertEquals(15, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEquals(99, self.org.get_credits_expiring_soon())
            self.assertEquals(15, self.org.get_low_credits_threshold())

        # some cedits expires but more credits will remain active
        later_active_topup = TopUp.create(self.admin, price=0, credits=200)
        five_week_ahead = timezone.now() + relativedelta(days=35)
        later_active_topup.expires_on = five_week_ahead
        later_active_topup.save(update_fields=['expires_on'])
        self.org.update_caches(OrgEvent.topup_updated, None)
        self.org.apply_topups()

        with self.assertNumQueries(3):
            self.assertEquals(0, self.org.get_credits_expiring_soon())

        with self.assertNumQueries(1):
            self.assertEquals(45, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEquals(0, self.org.get_credits_expiring_soon())
            self.assertEquals(45, self.org.get_low_credits_threshold())

        # no expiring credits
        gift_topup.expires_on = five_week_ahead
        gift_topup.save(update_fields=['expires_on'])
        self.org.update_caches(OrgEvent.topup_updated, None)
        self.org.apply_topups()

        with self.assertNumQueries(3):
            self.assertEquals(0, self.org.get_credits_expiring_soon())

        with self.assertNumQueries(1):
            self.assertEquals(45, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEquals(0, self.org.get_credits_expiring_soon())
            self.assertEquals(45, self.org.get_low_credits_threshold())

        # do not consider expired topup
        gift_topup.expires_on = yesterday
        gift_topup.save(update_fields=['expires_on'])
        self.org.update_caches(OrgEvent.topup_updated, None)
        self.org.apply_topups()

        with self.assertNumQueries(3):
            self.assertEquals(0, self.org.get_credits_expiring_soon())

        with self.assertNumQueries(1):
            self.assertEquals(30, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEquals(0, self.org.get_credits_expiring_soon())
            self.assertEquals(30, self.org.get_low_credits_threshold())

        TopUp.objects.all().update(is_active=False)
        self.org.update_caches(OrgEvent.topup_updated, None)
        self.org.apply_topups()

        with self.assertNumQueries(1):
            self.assertEquals(0, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEquals(0, self.org.get_low_credits_threshold())

        # now buy some credits to make us multi user
        TopUp.create(self.admin, price=100, credits=100000)
        self.org.update_caches(OrgEvent.topup_updated, None)
        self.assertTrue(self.org.is_multi_user_tier())
        self.assertFalse(self.org.is_multi_org_tier())

        # good deal!
        TopUp.create(self.admin, price=100, credits=1000000)
        self.org.update_caches(OrgEvent.topup_updated, None)
        self.assertTrue(self.org.is_multi_user_tier())
        self.assertTrue(self.org.is_multi_org_tier())

    @patch('temba.orgs.views.TwilioRestClient', MockTwilioClient)
    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_twilio_connect(self):

        with patch('temba.tests.MockTwilioClient.MockAccounts.get') as mock_get:
            with patch('temba.tests.MockTwilioClient.MockApplications.list') as mock_apps_list:

                org = self.org

                connect_url = reverse("orgs.org_twilio_connect")

                self.login(self.admin)
                self.admin.set_org(self.org)

                response = self.client.get(connect_url)
                self.assertEquals(200, response.status_code)
                self.assertTrue(response.context['form'])
                self.assertEquals(len(response.context['form'].fields.keys()), 3)
                self.assertIn('account_sid', response.context['form'].fields.keys())
                self.assertIn('account_token', response.context['form'].fields.keys())

                mock_get.return_value = MockTwilioClient.MockAccount('Full')
                mock_apps_list.return_value = [MockTwilioClient.MockApplication("%s/%d" % (settings.TEMBA_HOST.lower(),
                                                                                           self.org.pk))]

                # try posting without an account token
                post_data = dict()
                post_data['account_sid'] = "AccountSid"
                response = self.client.post(connect_url, post_data)
                self.assertEquals(response.context['form'].errors['account_token'][0], 'This field is required.')

                # now add the account token and try again
                post_data['account_token'] = "AccountToken"

                # but with an unexpected exception
                with patch('temba.tests.MockTwilioClient.__init__') as mock:
                    mock.side_effect = Exception('Unexpected')
                    response = self.client.post(connect_url, post_data)
                    self.assertEquals('The Twilio account SID and Token seem invalid. '
                                      'Please check them again and retry.',
                                      response.context['form'].errors['__all__'][0])

                self.client.post(connect_url, post_data)
                org.refresh_from_db()
                self.assertEquals(org.config_json()['ACCOUNT_SID'], "AccountSid")
                self.assertEquals(org.config_json()['ACCOUNT_TOKEN'], "AccountToken")
                self.assertTrue(org.config_json()['APPLICATION_SID'])

                # when the user submit the secondary token, we use it to get the primary one from the rest API
                with patch('temba.tests.MockTwilioClient.MockAccounts.get') as mock_get_primary:
                    mock_get_primary.return_value = MockTwilioClient.MockAccount('Full', 'PrimaryAccountToken')

                    self.client.post(connect_url, post_data)
                    org.refresh_from_db()
                    self.assertEquals(org.config_json()['ACCOUNT_SID'], "AccountSid")
                    self.assertEquals(org.config_json()['ACCOUNT_TOKEN'], "PrimaryAccountToken")
                    self.assertTrue(org.config_json()['APPLICATION_SID'])

                    twilio_account_url = reverse('orgs.org_twilio_account')
                    response = self.client.get(twilio_account_url)
                    self.assertEquals("AccountSid", response.context['account_sid'])

                    org.refresh_from_db()
                    config = org.config_json()
                    self.assertEquals('AccountSid', config['ACCOUNT_SID'])
                    self.assertEquals('PrimaryAccountToken', config['ACCOUNT_TOKEN'])

                    # post without a sid or token, should get a form validation error
                    response = self.client.post(twilio_account_url, dict(disconnect='false'), follow=True)
                    self.assertEquals('[{"message": "You must enter your Twilio Account SID", "code": ""}]',
                                      response.context['form'].errors['__all__'].as_json())

                    # all our twilio creds should remain the same
                    org.refresh_from_db()
                    config = org.config_json()
                    self.assertEquals(config['ACCOUNT_SID'], "AccountSid")
                    self.assertEquals(config['ACCOUNT_TOKEN'], "PrimaryAccountToken")
                    self.assertEquals(config['APPLICATION_SID'], "TwilioTestSid")

                    # now try with all required fields, and a bonus field we shouldn't change
                    self.client.post(twilio_account_url, dict(account_sid='AccountSid',
                                                              account_token='SecondaryToken',
                                                              disconnect='false',
                                                              name='DO NOT CHANGE ME'), follow=True)
                    # name shouldn't change
                    org.refresh_from_db()
                    self.assertEquals(org.name, "Temba")

                    # now disconnect our twilio connection
                    self.assertTrue(org.is_connected_to_twilio())
                    self.client.post(twilio_account_url, dict(disconnect='true', follow=True))

                    org.refresh_from_db()
                    self.assertFalse(org.is_connected_to_twilio())

    def test_has_airtime_transfers(self):
        AirtimeTransfer.objects.filter(org=self.org).delete()
        self.assertFalse(self.org.has_airtime_transfers())
        contact = self.create_contact('Bob', number='+250788123123')

        AirtimeTransfer.objects.create(org=self.org, recipient='+250788123123', amount='100',
                                       contact=contact, created_by=self.admin, modified_by=self.admin)

        self.assertTrue(self.org.has_airtime_transfers())

    def test_transferto_model_methods(self):
        org = self.org

        org.refresh_from_db()
        self.assertFalse(org.is_connected_to_transferto())

        org.connect_transferto('login', 'token', self.admin)

        org.refresh_from_db()
        self.assertTrue(org.is_connected_to_transferto())
        self.assertEqual(org.modified_by, self.admin)

        org.remove_transferto_account(self.admin)

        org.refresh_from_db()
        self.assertFalse(org.is_connected_to_transferto())
        self.assertEqual(org.modified_by, self.admin)

    def test_transferto_account(self):
        self.login(self.admin)

        # connect transferTo
        transferto_account_url = reverse('orgs.org_transfer_to_account')

        with patch('temba.airtime.models.AirtimeTransfer.post_transferto_api_response') as mock_post_transterto_request:
            mock_post_transterto_request.return_value = MockResponse(200, 'Unexpected content')
            response = self.client.post(transferto_account_url, dict(account_login='login', airtime_api_token='token',
                                                                     disconnect='false'))

            self.assertContains(response, "Your TransferTo API key and secret seem invalid.")
            self.assertFalse(self.org.is_connected_to_transferto())

            mock_post_transterto_request.return_value = MockResponse(200, 'authentication_key=123\r\n'
                                                                          'error_code=400\r\n'
                                                                          'error_txt=Failed Authentication\r\n')

            response = self.client.post(transferto_account_url, dict(account_login='login', airtime_api_token='token',
                                                                     disconnect='false'))

            self.assertContains(response, "Connecting to your TransferTo account failed "
                                          "with error text: Failed Authentication")

            self.assertFalse(self.org.is_connected_to_transferto())

            mock_post_transterto_request.return_value = MockResponse(200, 'info_txt=pong\r\n'
                                                                          'authentication_key=123\r\n'
                                                                          'error_code=0\r\n'
                                                                          'error_txt=Transaction successful\r\n')

            response = self.client.post(transferto_account_url, dict(account_login='login', airtime_api_token='token',
                                                                     disconnect='false'))
            self.assertNoFormErrors(response)
            # transferTo should be connected
            self.org = Org.objects.get(pk=self.org.pk)
            self.assertTrue(self.org.is_connected_to_transferto())
            self.assertEqual(self.org.config_json()['TRANSFERTO_ACCOUNT_LOGIN'], 'login')
            self.assertEqual(self.org.config_json()['TRANSFERTO_AIRTIME_API_TOKEN'], 'token')

            response = self.client.get(transferto_account_url)
            self.assertEqual(response.context['transferto_account_login'], 'login')

            # and disconnect
            response = self.client.post(transferto_account_url, dict(account_login='login', airtime_api_token='token',
                                                                     disconnect='true'))

            self.assertNoFormErrors(response)
            self.org = Org.objects.get(pk=self.org.pk)
            self.assertFalse(self.org.is_connected_to_transferto())
            self.assertFalse(self.org.config_json()['TRANSFERTO_ACCOUNT_LOGIN'])
            self.assertFalse(self.org.config_json()['TRANSFERTO_AIRTIME_API_TOKEN'])

            mock_post_transterto_request.side_effect = Exception('foo')
            response = self.client.post(transferto_account_url, dict(account_login='login', airtime_api_token='token',
                                                                     disconnect='false'))
            self.assertContains(response, "Your TransferTo API key and secret seem invalid.")
            self.assertFalse(self.org.is_connected_to_transferto())

        # No account connected, do not show the button to Transfer logs
        response = self.client.get(transferto_account_url, HTTP_X_FORMAX=True)
        self.assertNotContains(response, reverse('airtime.airtimetransfer_list'))
        self.assertNotContains(response, "%s?disconnect=true" % reverse('orgs.org_transfer_to_account'))

        response = self.client.get(transferto_account_url)
        self.assertNotContains(response, reverse('airtime.airtimetransfer_list'))
        self.assertNotContains(response, "%s?disconnect=true" % reverse('orgs.org_transfer_to_account'))

        self.org.connect_transferto('login', 'token', self.admin)

        # links not show if request is not from formax
        response = self.client.get(transferto_account_url)
        self.assertNotContains(response, reverse('airtime.airtimetransfer_list'))
        self.assertNotContains(response, "%s?disconnect=true" % reverse('orgs.org_transfer_to_account'))

        # link show for formax requests
        response = self.client.get(transferto_account_url, HTTP_X_FORMAX=True)
        self.assertContains(response, reverse('airtime.airtimetransfer_list'))
        self.assertContains(response, "%s?disconnect=true" % reverse('orgs.org_transfer_to_account'))

    def test_resthooks(self):
        # no hitting this page without auth
        resthook_url = reverse('orgs.org_resthooks')
        response = self.client.get(resthook_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # get our resthook management page
        response = self.client.get(resthook_url)

        # shouldn't have any resthooks listed yet
        self.assertFalse(response.context['current_resthooks'])

        # ok, let's create one
        self.client.post(resthook_url, dict(resthook='mother-registration'))

        # should now have a resthook
        resthook = Resthook.objects.get()
        self.assertEqual(resthook.slug, 'mother-registration')
        self.assertEqual(resthook.org, self.org)
        self.assertEqual(resthook.created_by, self.admin)

        # fetch our read page, should have have our resthook
        response = self.client.get(resthook_url)
        self.assertTrue(response.context['current_resthooks'])

        # let's try to create a repeat, should fail due to duplicate slug
        response = self.client.post(resthook_url, dict(resthook='Mother-Registration'))
        self.assertTrue(response.context['form'].errors)

        # hit our list page used by select2, checking it lists our resthook
        response = self.client.get(reverse('api.resthook_list') + "?_format=select2")
        results = json.loads(response.content)['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], dict(text='mother-registration', id='mother-registration'))

        # finally, let's remove that resthook
        self.client.post(resthook_url, {'resthook_%d' % resthook.id: 'checked'})
        resthook.refresh_from_db()
        self.assertFalse(resthook.is_active)

        # no more resthooks!
        response = self.client.get(resthook_url)
        self.assertFalse(response.context['current_resthooks'])

    def test_connect_nexmo(self):
        self.login(self.admin)

        # connect nexmo
        connect_url = reverse('orgs.org_nexmo_connect')

        # simulate invalid credentials
        with patch('requests.get') as nexmo:
            nexmo.return_value = MockResponse(401, '{"error-code": "401"}')
            response = self.client.post(connect_url, dict(api_key='key', api_secret='secret'))
            self.assertContains(response, "Your Nexmo API key and secret seem invalid.")
            self.assertFalse(self.org.is_connected_to_nexmo())

        # ok, now with a success
        with patch('requests.get') as nexmo_get:
            with patch('requests.post') as nexmo_post:
                # believe it or not nexmo returns 'error-code' 200
                nexmo_get.return_value = MockResponse(200, '{"error-code": "200"}')
                nexmo_post.return_value = MockResponse(200, '{"error-code": "200"}')
                self.client.post(connect_url, dict(api_key='key', api_secret='secret'))

                # nexmo should now be connected
                self.org = Org.objects.get(pk=self.org.pk)
                self.assertTrue(self.org.is_connected_to_nexmo())
                self.assertEquals(self.org.config_json()['NEXMO_KEY'], 'key')
                self.assertEquals(self.org.config_json()['NEXMO_SECRET'], 'secret')

        # and disconnect
        self.org.remove_nexmo_account(self.admin)
        self.assertFalse(self.org.is_connected_to_nexmo())
        self.assertFalse(self.org.config_json()['NEXMO_KEY'])
        self.assertFalse(self.org.config_json()['NEXMO_SECRET'])

    def test_nexmo_configuration(self):
        self.login(self.admin)

        nexmo_configuration_url = reverse('orgs.org_nexmo_configuration')

        # try nexmo not connected
        response = self.client.get(nexmo_configuration_url)

        self.assertEqual(response.status_code, 302)
        response = self.client.get(nexmo_configuration_url, follow=True)

        self.assertEqual(response.request['PATH_INFO'], reverse('orgs.org_nexmo_connect'))

        self.org.connect_nexmo('key', 'secret', self.admin)

        with patch('temba.nexmo.NexmoClient.update_account') as mock_update_account:
            # try automatic nexmo settings update
            mock_update_account.return_value = True

            response = self.client.get(nexmo_configuration_url)
            self.assertEqual(response.status_code, 302)

            response = self.client.get(nexmo_configuration_url, follow=True)
            self.assertEqual(response.request['PATH_INFO'], reverse('channels.channel_claim_nexmo'))

        with patch('temba.nexmo.NexmoClient.update_account') as mock_update_account:
            mock_update_account.side_effect = [NexmoValidationError, NexmoValidationError]

            response = self.client.get(nexmo_configuration_url)
            self.assertEqual(response.status_code, 200)

            response = self.client.get(nexmo_configuration_url, follow=True)
            self.assertEqual(response.request['PATH_INFO'], reverse('orgs.org_nexmo_configuration'))

    def test_connect_plivo(self):
        self.login(self.admin)

        # connect plivo
        connect_url = reverse('orgs.org_plivo_connect')

        # simulate invalid credentials
        with patch('requests.get') as plivo_mock:
            plivo_mock.return_value = MockResponse(401,
                                                   'Could not verify your access level for that URL.'
                                                   '\nYou have to login with proper credentials')
            response = self.client.post(connect_url, dict(auth_id='auth-id', auth_token='auth-token'))
            self.assertContains(response,
                                "Your Plivo AUTH ID and AUTH TOKEN seem invalid. Please check them again and retry.")
            self.assertFalse(Channel.CONFIG_PLIVO_AUTH_ID in self.client.session)
            self.assertFalse(Channel.CONFIG_PLIVO_AUTH_TOKEN in self.client.session)

        # ok, now with a success
        with patch('requests.get') as plivo_mock:
            plivo_mock.return_value = MockResponse(200, json.dumps(dict()))
            self.client.post(connect_url, dict(auth_id='auth-id', auth_token='auth-token'))

            # plivo should be added to the session
            self.assertEquals(self.client.session[Channel.CONFIG_PLIVO_AUTH_ID], 'auth-id')
            self.assertEquals(self.client.session[Channel.CONFIG_PLIVO_AUTH_TOKEN], 'auth-token')

    def test_download(self):
        response = self.client.get('/org/download/messages/123/')
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get('/org/download/messages/123/')
        self.assertRedirect(response, '/assets/download/message_export/123/')

        response = self.client.get('/org/download/contacts/123/')
        self.assertRedirect(response, '/assets/download/contact_export/123/')

        response = self.client.get('/org/download/flows/123/')
        self.assertRedirect(response, '/assets/download/results_export/123/')

    def test_tiers(self):

        # not enough credits with tiers enabled
        settings.BRANDING[settings.DEFAULT_BRAND]['tiers'] = dict(multi_org=1000000)
        self.assertIsNone(self.org.create_sub_org('Sub Org A'))

        # not enough credits, but tiers disabled
        settings.BRANDING[settings.DEFAULT_BRAND]['tiers'] = dict(multi_org=0)
        self.assertIsNotNone(self.org.create_sub_org('Sub Org A'))

        # tiers enabled, but enough credits
        settings.BRANDING[settings.DEFAULT_BRAND]['tiers'] = dict(multi_org=1000000)
        TopUp.create(self.admin, price=100, credits=1000000)
        self.org.update_caches(OrgEvent.topup_updated, None)
        self.assertIsNotNone(self.org.create_sub_org('Sub Org B'))

    def test_sub_orgs(self):

        from temba.orgs.models import Debit
        settings.BRANDING[settings.DEFAULT_BRAND]['tiers'] = dict(multi_org=1000000)

        # lets start with two topups
        expires = timezone.now() + timedelta(days=400)
        first_topup = TopUp.objects.filter(org=self.org).first()
        second_topup = TopUp.create(self.admin, price=0, credits=1000, org=self.org, expires_on=expires)

        sub_org = self.org.create_sub_org('Sub Org')

        # we won't create sub orgs if the org isn't the proper level
        self.assertIsNone(sub_org)

        # lower the tier and try again
        settings.BRANDING[settings.DEFAULT_BRAND]['tiers'] = dict(multi_org=0)
        sub_org = self.org.create_sub_org('Sub Org')

        # suborgs can't create suborgs
        self.assertIsNone(sub_org.create_sub_org('Grandchild Org'))

        # we should be linked to our parent with the same brand
        self.assertEqual(self.org, sub_org.parent)
        self.assertEqual(self.org.brand, sub_org.brand)

        # our sub account should have zero credits
        self.assertEqual(0, sub_org.get_credits_remaining())

        # default values should be the same as parent
        self.assertEqual(self.org.timezone, sub_org.timezone)
        self.assertEqual(self.org.created_by, sub_org.created_by)

        # now allocate some credits to our sub org
        self.assertTrue(self.org.allocate_credits(self.admin, sub_org, 700))
        self.assertEqual(700, sub_org.get_credits_remaining())
        self.assertEqual(1300, self.org.get_credits_remaining())

        # we should have a debit to track this transaction
        debits = Debit.objects.filter(topup__org=self.org)
        self.assertEqual(1, len(debits))

        debit = debits.first()
        self.assertEqual(700, debit.amount)
        self.assertEqual(Debit.TYPE_ALLOCATION, debit.debit_type)
        self.assertEqual(first_topup.expires_on, debit.beneficiary.expires_on)

        # try allocating more than we have
        self.assertFalse(self.org.allocate_credits(self.admin, sub_org, 1301))
        self.assertEqual(700, sub_org.get_credits_remaining())
        self.assertEqual(1300, self.org.get_credits_remaining())
        self.assertEqual(700, self.org._calculate_credits_used())

        # now allocate across our remaining topups
        self.assertTrue(self.org.allocate_credits(self.admin, sub_org, 1200))
        self.assertEqual(1900, sub_org.get_credits_remaining())
        self.assertEqual(1900, self.org.get_credits_used())
        self.assertEqual(100, self.org.get_credits_remaining())

        # now clear our cache, we ought to have proper amount still
        self.org._calculate_credit_caches()
        sub_org._calculate_credit_caches()

        self.assertEqual(1900, sub_org.get_credits_remaining())
        self.assertEqual(100, self.org.get_credits_remaining())

        # this creates two more debits, for a total of three
        debits = Debit.objects.filter(topup__org=self.org).order_by('id')
        self.assertEqual(3, len(debits))

        # the last two debits should expire at same time as topup they were funded by
        self.assertEqual(first_topup.expires_on, debits[1].topup.expires_on)
        self.assertEqual(second_topup.expires_on, debits[2].topup.expires_on)

        # allocate the exact number of credits remaining
        self.org.allocate_credits(self.admin, sub_org, 100)
        self.assertEqual(2000, sub_org.get_credits_remaining())
        self.assertEqual(0, self.org.get_credits_remaining())

    def test_sub_org_ui(self):

        self.login(self.admin)

        settings.BRANDING[settings.DEFAULT_BRAND]['tiers'] = dict(multi_org=1000000)

        # set our org on the session
        session = self.client.session
        session['org_id'] = self.org.id
        session.save()

        response = self.client.get(reverse('orgs.org_home'))
        self.assertNotContains(response, 'Manage Organizations')

        # attempting to manage orgs should redirect
        response = self.client.get(reverse('orgs.org_sub_orgs'))
        self.assertRedirect(response, reverse('orgs.org_home'))

        # creating a new sub org should also redirect
        response = self.client.get(reverse('orgs.org_create_sub_org'))
        self.assertRedirect(response, reverse('orgs.org_home'))

        # make sure posting is gated too
        new_org = dict(name='Sub Org', timezone=self.org.timezone, date_format=self.org.date_format)
        response = self.client.post(reverse('orgs.org_create_sub_org'), new_org)
        self.assertRedirect(response, reverse('orgs.org_home'))

        # same thing with trying to transfer credits
        response = self.client.get(reverse('orgs.org_transfer_credits'))
        self.assertRedirect(response, reverse('orgs.org_home'))

        # cant manage users either
        response = self.client.get(reverse('orgs.org_manage_accounts_sub_org'))
        self.assertRedirect(response, reverse('orgs.org_home'))

        # zero out our tier
        settings.BRANDING[settings.DEFAULT_BRAND]['tiers'] = dict(multi_org=0)
        self.assertTrue(self.org.is_multi_org_tier())
        response = self.client.get(reverse('orgs.org_home'))
        self.assertContains(response, 'Manage Organizations')

        # now we can manage our orgs
        response = self.client.get(reverse('orgs.org_sub_orgs'))
        self.assertEqual(200, response.status_code)
        self.assertContains(response, 'Organizations')

        # add a sub org
        response = self.client.post(reverse('orgs.org_create_sub_org'), new_org)
        self.assertRedirect(response, reverse('orgs.org_sub_orgs'))
        sub_org = Org.objects.filter(name='Sub Org').first()
        self.assertIsNotNone(sub_org)
        self.assertIn(self.admin, sub_org.administrators.all())

        # load the transfer credit page
        response = self.client.get(reverse('orgs.org_transfer_credits'))
        self.assertEqual(200, response.status_code)

        # try to transfer more than we have
        post_data = dict(from_org=self.org.id, to_org=sub_org.id, amount=1500)
        response = self.client.post(reverse('orgs.org_transfer_credits'), post_data)
        self.assertContains(response, "Pick a different organization to transfer from")

        # now transfer some creditos
        post_data = dict(from_org=self.org.id, to_org=sub_org.id, amount=600)
        response = self.client.post(reverse('orgs.org_transfer_credits'), post_data)

        self.assertEqual(400, self.org.get_credits_remaining())
        self.assertEqual(600, sub_org.get_credits_remaining())

        # we can reach the manage accounts page too now
        response = self.client.get('%s?org=%d' % (reverse('orgs.org_manage_accounts_sub_org'), sub_org.id))
        self.assertEqual(200, response.status_code)

        # edit our sub org's name
        new_org['name'] = 'New Sub Org Name'
        new_org['slug'] = 'new-sub-org-name'
        response = self.client.post('%s?org=%s' % (reverse('orgs.org_edit_sub_org'), sub_org.pk), new_org)
        self.assertIsNotNone(Org.objects.filter(name='New Sub Org Name').first())

        # now we should see new topups on our sub org
        session['org_id'] = sub_org.id
        session.save()

        response = self.client.get(reverse('orgs.topup_list'))
        self.assertContains(response, '600 Credits')


class AnonOrgTest(TembaTest):
    """
    Tests the case where our organization is marked as anonymous, that is the phone numbers are masked
    for users.
    """

    def setUp(self):
        super(AnonOrgTest, self).setUp()

        self.org.is_anon = True
        self.org.save()

    def test_contacts(self):
        # are there real phone numbers on the contact list page?
        contact = self.create_contact(None, "+250788123123")
        self.login(self.admin)

        masked = "%010d" % contact.pk

        response = self.client.get(reverse('contacts.contact_list'))

        # phone not in the list
        self.assertNotContains(response, "788 123 123")

        # but the id is
        self.assertContains(response, masked)

        # can't search for it
        response = self.client.get(reverse('contacts.contact_list') + "?search=788")

        # can't look for 788 as that is in the search box..
        self.assertNotContains(response, "123123")

        # create a flow
        flow = self.create_flow()

        # start the contact down it
        flow.start([], [contact])

        # should have one SMS
        self.assertEquals(1, Msg.all_messages.all().count())

        # shouldn't show the number on the outgoing page
        response = self.client.get(reverse('msgs.msg_outbox'))

        self.assertNotContains(response, "788 123 123")

        # also shouldn't show up on the flow results page
        response = self.client.get(reverse('flows.flow_results', args=[flow.pk]) + "?json=true")
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, masked)

        # create an incoming SMS, check our flow page
        Msg.create_incoming(self.channel, contact.get_urn().urn, "Blue")
        response = self.client.get(reverse('msgs.msg_flow'))
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, masked)

        # send another, this will be in our inbox this time
        Msg.create_incoming(self.channel, contact.get_urn().urn, "Where's the beef?")
        response = self.client.get(reverse('msgs.msg_flow'))
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, masked)

        # contact detail page
        response = self.client.get(reverse('contacts.contact_read', args=[contact.uuid]))
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, masked)


class OrgCRUDLTest(TembaTest):

    def test_org_grant(self):
        grant_url = reverse('orgs.org_grant')
        response = self.client.get(grant_url)
        self.assertRedirect(response, '/users/login/')

        self.user = self.create_user(username="tito")

        self.login(self.user)
        response = self.client.get(grant_url)
        self.assertRedirect(response, '/users/login/')

        granters = Group.objects.get(name='Granters')
        self.user.groups.add(granters)

        response = self.client.get(grant_url)
        self.assertEquals(200, response.status_code)

        # fill out the form
        post_data = dict(email='john@carmack.com', first_name="John", last_name="Carmack",
                         name="Oculus", timezone="Africa/Kigali", credits="100000", password='dukenukem')
        response = self.client.post(grant_url, post_data, follow=True)

        self.assertContains(response, "created")

        org = Org.objects.get(name="Oculus")
        self.assertEquals(100000, org.get_credits_remaining())

        # check user exists and is admin
        User.objects.get(username="john@carmack.com")
        self.assertTrue(org.administrators.filter(username="john@carmack.com"))
        self.assertTrue(org.administrators.filter(username="tito"))

        # try a new org with a user that already exists instead
        del post_data['password']
        post_data['name'] = "id Software"

        response = self.client.post(grant_url, post_data, follow=True)

        self.assertContains(response, "created")

        org = Org.objects.get(name="id Software")
        self.assertEquals(100000, org.get_credits_remaining())

        self.assertTrue(org.administrators.filter(username="john@carmack.com"))
        self.assertTrue(org.administrators.filter(username="tito"))

    @patch("temba.orgs.views.OrgCRUDL.Signup.pre_process")
    def test_new_signup_with_user_logged_in(self, mock_pre_process):
        mock_pre_process.return_value = None
        signup_url = reverse('orgs.org_signup')
        self.user = self.create_user(username="tito")

        self.login(self.user)

        response = self.client.get(signup_url)
        self.assertEqual(response.status_code, 200)

        post_data = dict(first_name="Kellan", last_name="Alexander", email="kellan@example.com",
                         password="HeyThere", name="AlexCom", timezone="Africa/Kigali")

        response = self.client.post(signup_url, post_data)
        self.assertEqual(response.status_code, 302)

        # should have a new user
        user = User.objects.get(username="kellan@example.com")
        self.assertEqual(user.first_name, "Kellan")
        self.assertEqual(user.last_name, "Alexander")
        self.assertEqual(user.email, "kellan@example.com")
        self.assertTrue(user.check_password("HeyThere"))
        self.assertTrue(user.api_token)  # should be able to generate an API token

        # should have a new org
        org = Org.objects.get(name="AlexCom")
        self.assertEqual(org.timezone, "Africa/Kigali")

        # of which our user is an administrator
        self.assertTrue(org.get_org_admins().filter(pk=user.pk))

        # not the logged in user at the signup time
        self.assertFalse(org.get_org_admins().filter(pk=self.user.pk))

    def test_org_signup(self):
        signup_url = reverse('orgs.org_signup')
        response = self.client.get(signup_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('name', response.context['form'].fields)

        # submit with missing fields
        response = self.client.post(signup_url, {})
        self.assertFormError(response, 'form', 'name', "This field is required.")
        self.assertFormError(response, 'form', 'first_name', "This field is required.")
        self.assertFormError(response, 'form', 'last_name', "This field is required.")
        self.assertFormError(response, 'form', 'email', "This field is required.")
        self.assertFormError(response, 'form', 'password', "This field is required.")
        self.assertFormError(response, 'form', 'timezone', "This field is required.")

        # submit with invalid password and email
        post_data = dict(first_name="Eugene", last_name="Rwagasore", email="bad_email",
                         password="badpass", name="Your Face", timezone="Africa/Kigali")
        response = self.client.post(signup_url, post_data)
        self.assertFormError(response, 'form', 'email', "Enter a valid email address.")
        self.assertFormError(response, 'form', 'password', "Passwords must contain at least 8 letters.")

        # submit with valid data (long email)
        post_data = dict(first_name="Eugene", last_name="Rwagasore", email="myal12345678901234567890@relieves.org",
                         password="HelloWorld1", name="Relieves World", timezone="Africa/Kigali")
        response = self.client.post(signup_url, post_data)
        self.assertEqual(response.status_code, 302)

        # should have a new user
        user = User.objects.get(username="myal12345678901234567890@relieves.org")
        self.assertEqual(user.first_name, "Eugene")
        self.assertEqual(user.last_name, "Rwagasore")
        self.assertEqual(user.email, "myal12345678901234567890@relieves.org")
        self.assertTrue(user.check_password("HelloWorld1"))
        self.assertTrue(user.api_token)  # should be able to generate an API token

        # should have a new org
        org = Org.objects.get(name="Relieves World")
        self.assertEqual(org.timezone, "Africa/Kigali")
        self.assertEqual(str(org), "Relieves World")
        self.assertEqual(org.slug, "relieves-world")

        # of which our user is an administrator
        self.assertTrue(org.get_org_admins().filter(pk=user.pk))

        # org should have 1000 credits
        self.assertEqual(org.get_credits_remaining(), 1000)

        # from a single welcome topup
        topup = TopUp.objects.get(org=org)
        self.assertEqual(topup.credits, 1000)
        self.assertEqual(topup.price, 0)

        # fake session set_org to make the test work
        user.set_org(org)

        # should now be able to go to channels page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertEquals(200, response.status_code)

        # check that we have all the tabs
        self.assertContains(response, reverse('msgs.msg_inbox'))
        self.assertContains(response, reverse('flows.flow_list'))
        self.assertContains(response, reverse('contacts.contact_list'))
        self.assertContains(response, reverse('channels.channel_list'))
        self.assertContains(response, reverse('orgs.org_home'))

        post_data['name'] = "Relieves World Rwanda"
        response = self.client.post(signup_url, post_data)
        self.assertTrue('email' in response.context['form'].errors)

        # if we hit /login we'll be taken back to the channel page
        response = self.client.get(reverse('users.user_check_login'))
        self.assertRedirect(response, reverse('orgs.org_choose'))

        # but if we log out, same thing takes us to the login page
        self.client.logout()

        response = self.client.get(reverse('users.user_check_login'))
        self.assertRedirect(response, reverse('users.user_login'))

        # try going to the org home page, no dice
        response = self.client.get(reverse('orgs.org_home'))
        self.assertRedirect(response, reverse('users.user_login'))

        # log in as the user
        self.client.login(username="myal12345678901234567890@relieves.org", password="HelloWorld1")
        response = self.client.get(reverse('orgs.org_home'))

        self.assertEquals(200, response.status_code)

        # try setting our webhook and subscribe to one of the events
        response = self.client.post(reverse('orgs.org_webhook'), dict(webhook='http://fake.com/webhook.php', mt_sms=1))
        self.assertRedirect(response, reverse('orgs.org_home'))

        org = Org.objects.get(name="Relieves World")
        self.assertEquals("http://fake.com/webhook.php", org.get_webhook_url())
        self.assertTrue(org.is_notified_of_mt_sms())
        self.assertFalse(org.is_notified_of_mo_sms())
        self.assertFalse(org.is_notified_of_mt_call())
        self.assertFalse(org.is_notified_of_mo_call())
        self.assertFalse(org.is_notified_of_alarms())

        # try changing our username, wrong password
        post_data = dict(email='myal@wr.org', current_password='HelloWorld')
        response = self.client.post(reverse('orgs.user_edit'), post_data)
        self.assertEquals(200, response.status_code)
        self.assertTrue('current_password' in response.context['form'].errors)

        # bad new password
        post_data = dict(email='myal@wr.org', current_password='HelloWorld1', new_password='passwor')
        response = self.client.post(reverse('orgs.user_edit'), post_data)
        self.assertEquals(200, response.status_code)
        self.assertTrue('new_password' in response.context['form'].errors)

        User.objects.create(username='bill@msn.com', email='bill@msn.com')

        # dupe user
        post_data = dict(email='bill@msn.com', current_password='HelloWorld1')
        response = self.client.post(reverse('orgs.user_edit'), post_data)
        self.assertEquals(200, response.status_code)
        self.assertTrue('email' in response.context['form'].errors)

        post_data = dict(email='myal@wr.org', first_name="Myal", last_name="Greene", language="en-us", current_password='HelloWorld1')
        response = self.client.post(reverse('orgs.user_edit'), post_data)
        self.assertRedirect(response, reverse('orgs.org_home'))

        self.assertTrue(User.objects.get(username='myal@wr.org'))
        self.assertTrue(User.objects.get(email='myal@wr.org'))
        self.assertFalse(User.objects.filter(username='myal@relieves.org'))
        self.assertFalse(User.objects.filter(email='myal@relieves.org'))

        post_data['current_password'] = 'HelloWorld1'
        post_data['new_password'] = 'Password123'
        response = self.client.post(reverse('orgs.user_edit'), post_data)
        self.assertRedirect(response, reverse('orgs.org_home'))

        user = User.objects.get(username='myal@wr.org')
        self.assertTrue(user.check_password('Password123'))

    def test_org_timezone(self):
        self.assertEqual(self.org.timezone, 'Africa/Kigali')

        Msg.create_incoming(self.channel, "tel:250788382382", "My name is Frank")

        self.login(self.admin)
        response = self.client.get(reverse('msgs.msg_inbox'), follow=True)

        # Check the message datetime
        created_on = response.context['object_list'][0].created_on.astimezone(timezone.pytz.timezone(self.org.timezone))
        self.assertIn(created_on.strftime("%I:%M %p").lower().lstrip('0'), response.content)

        # change the org timezone to "Africa/Kenya"
        self.org.timezone = 'Africa/Nairobi'
        self.org.save()

        response = self.client.get(reverse('msgs.msg_inbox'), follow=True)

        # checkout the message should have the datetime changed by timezone
        created_on = response.context['object_list'][0].created_on.astimezone(timezone.pytz.timezone(self.org.timezone))
        self.assertIn(created_on.strftime("%I:%M %p").lower().lstrip('0'), response.content)

    def test_urn_schemes(self):
        # remove existing channels
        Channel.objects.all().update(is_active=False, org=None)

        self.assertEqual(set(), self.org.get_schemes(Channel.ROLE_SEND))
        self.assertEqual(set(), self.org.get_schemes(Channel.ROLE_RECEIVE))

        # add a receive only tel channel
        Channel.create(self.org, self.user, 'RW', Channel.TYPE_TWILIO, "Nexmo", "0785551212", role="R", secret="45678", gcm_id="123")

        self.org = Org.objects.get(pk=self.org.pk)
        self.assertEqual(set(), self.org.get_schemes(Channel.ROLE_SEND))
        self.assertEqual({TEL_SCHEME}, self.org.get_schemes(Channel.ROLE_RECEIVE))

        # add a send/receive tel channel
        Channel.create(self.org, self.user, 'RW', Channel.TYPE_TWILIO, "Twilio", "0785553434", role="SR", secret="56789", gcm_id="456")
        self.org = Org.objects.get(pk=self.org.id)
        self.assertEqual({TEL_SCHEME}, self.org.get_schemes(Channel.ROLE_SEND))
        self.assertEqual({TEL_SCHEME}, self.org.get_schemes(Channel.ROLE_RECEIVE))

        # add a twitter channel
        Channel.create(self.org, self.user, None, Channel.TYPE_TWITTER, "Twitter")
        self.org = Org.objects.get(pk=self.org.id)
        self.assertEqual({TEL_SCHEME, TWITTER_SCHEME}, self.org.get_schemes(Channel.ROLE_SEND))
        self.assertEqual({TEL_SCHEME, TWITTER_SCHEME}, self.org.get_schemes(Channel.ROLE_RECEIVE))

    def test_login_case_not_sensitive(self):
        login_url = reverse('users.user_login')

        User.objects.create_superuser("superuser", "superuser@group.com", "superuser")

        response = self.client.post(login_url, dict(username="superuser", password="superuser"))
        self.assertEquals(response.status_code, 302)

        response = self.client.post(login_url, dict(username="superuser", password="superuser"), follow=True)
        self.assertEquals(response.request['PATH_INFO'], reverse('orgs.org_manage'))

        response = self.client.post(login_url, dict(username="SUPeruser", password="superuser"))
        self.assertEquals(response.status_code, 302)

        response = self.client.post(login_url, dict(username="SUPeruser", password="superuser"), follow=True)
        self.assertEquals(response.request['PATH_INFO'], reverse('orgs.org_manage'))

        User.objects.create_superuser("withCAPS", "with_caps@group.com", "thePASSWORD")

        response = self.client.post(login_url, dict(username="withcaps", password="thePASSWORD"))
        self.assertEquals(response.status_code, 302)

        response = self.client.post(login_url, dict(username="withcaps", password="thePASSWORD"), follow=True)
        self.assertEquals(response.request['PATH_INFO'], reverse('orgs.org_manage'))

        # passwords stay case sensitive
        response = self.client.post(login_url, dict(username="withcaps", password="thepassword"), follow=True)
        self.assertTrue('form' in response.context)
        self.assertTrue(response.context['form'].errors)

    def test_org_service(self):
        # create a customer service user
        self.csrep = self.create_user("csrep")
        self.csrep.groups.add(Group.objects.get(name="Customer Support"))
        self.csrep.is_staff = True
        self.csrep.save()

        service_url = reverse('orgs.org_service')

        # without logging in, try to service our main org
        response = self.client.post(service_url, dict(organization=self.org.id))
        self.assertRedirect(response, '/users/login/')

        # try logging in with a normal user
        self.login(self.admin)

        # same thing, no permission
        response = self.client.post(service_url, dict(organization=self.org.id))
        self.assertRedirect(response, '/users/login/')

        # ok, log in as our cs rep
        self.login(self.csrep)

        # then service our org
        response = self.client.post(service_url, dict(organization=self.org.id))
        self.assertRedirect(response, '/msg/inbox/')

        # create a new contact
        response = self.client.post(reverse('contacts.contact_create'), data=dict(name='Ben Haggerty',
                                                                                  urn__tel__0='0788123123'))
        self.assertNoFormErrors(response)

        # make sure that contact's created on is our cs rep
        contact = Contact.objects.get(urns__path='+250788123123', org=self.org)
        self.assertEquals(self.csrep, contact.created_by)

        # make sure we can manage topups as well
        TopUp.objects.create(org=self.org, price=100, credits=1000, expires_on=timezone.now() + timedelta(days=30),
                             created_by=self.admin, modified_by=self.admin)

        response = self.client.get(reverse('orgs.topup_manage') + "?org=%d" % self.org.id)

        # i'd buy that for a dollar!
        self.assertContains(response, '$1.00')
        self.assertNotRedirect(response, '/users/login/')

        # ok, now end our session
        response = self.client.post(service_url, dict())
        self.assertRedirect(response, '/org/manage/')

        # can no longer go to inbox, asked to log in
        response = self.client.get(reverse('msgs.msg_inbox'))
        self.assertRedirect(response, '/users/login/')


class LanguageTest(TembaTest):

    def test_languages(self):
        url = reverse('orgs.org_languages')

        self.login(self.admin)

        # update our org with some language settings
        response = self.client.post(url, dict(primary_lang='fre', languages='hat,arc'))
        self.assertEqual(response.status_code, 302)
        self.org.refresh_from_db()

        self.assertEqual(self.org.primary_language.name, 'French')
        self.assertIsNotNone(self.org.languages.filter(name='French'))

        # everything after the paren should be stripped for aramaic
        self.assertIsNotNone(self.org.languages.filter(name='Official Aramaic'))

        # everything after the semi should be stripped for haitian
        self.assertIsNotNone(self.org.languages.filter(name='Haitian'))

        # check that the last load shows our new languages
        response = self.client.get(url)
        self.assertEqual(response.context['languages'], 'Haitian and Official Aramaic')
        self.assertContains(response, 'fre')
        self.assertContains(response, 'hat,arc')

        # three translation languages
        self.client.post(url, dict(primary_lang='fre', languages='hat,arc,spa'))
        response = self.client.get(reverse('orgs.org_languages'))
        self.assertEqual(response.context['languages'], 'Haitian, Official Aramaic and Spanish')

        # one translation language
        self.client.post(url, dict(primary_lang='fre', languages='hat'))
        response = self.client.get(reverse('orgs.org_languages'))
        self.assertEqual(response.context['languages'], 'Haitian')

        # remove all languages
        self.client.post(url, dict())
        self.org.refresh_from_db()
        self.assertIsNone(self.org.primary_language)
        self.assertFalse(self.org.languages.all())

        # search languages
        response = self.client.get('%s?search=fre' % url)
        results = json.loads(response.content)['results']
        self.assertEqual(len(results), 4)

        # initial should do a match on code only
        response = self.client.get('%s?initial=fre' % url)
        results = json.loads(response.content)['results']
        self.assertEqual(len(results), 1)

    def test_language_codes(self):
        self.assertEquals('French', languages.get_language_name('fre'))
        self.assertEquals('Creoles and pidgins, English based', languages.get_language_name('cpe'))

        # should strip off anything after an open paren or semicolon
        self.assertEquals('Official Aramaic', languages.get_language_name('arc'))
        self.assertEquals('Haitian', languages.get_language_name('hat'))

        # check that search returns results and in the proper order
        matches = languages.search_language_names('Fre')
        self.assertEquals(4, len(matches))
        self.assertEquals('Creoles and pidgins, French-based', matches[0]['text'])
        self.assertEquals('French', matches[1]['text'])
        self.assertEquals('French, Middle (ca.1400-1600)', matches[2]['text'])
        self.assertEquals('French, Old (842-ca.1400)', matches[3]['text'])

        # try a language that doesn't exist
        self.assertEquals(None, languages.get_language_name('klingon'))

    def test_get_localized_text(self):
        text_translations = dict(eng="Hello", esp="Hola")

        # null case
        self.assertEqual(Language.get_localized_text(None, None, "Hi"), "Hi")

        # simple dictionary case
        self.assertEqual(Language.get_localized_text(text_translations, ['eng'], "Hi"), "Hello")

        # missing language case
        self.assertEqual(Language.get_localized_text(text_translations, ['fre'], "Hi"), "Hi")

        # secondary option
        self.assertEqual(Language.get_localized_text(text_translations, ['fre', 'esp'], "Hi"), "Hola")


class BulkExportTest(TembaTest):

    def test_get_dependencies(self):

        # import a flow that triggers another flow
        contact1 = self.create_contact("Marshawn", "+14255551212")
        substitutions = dict(contact_id=contact1.id)
        flow = self.get_flow('triggered', substitutions)

        # read in the old version 8 raw json
        old_json = json.loads(self.get_import_json('triggered', substitutions))
        old_actions = old_json['flows'][1]['action_sets'][0]['actions']

        # splice our actionset with old bits
        actionset = flow.action_sets.all()[0]
        actionset.actions = json.dumps(old_actions)
        actionset.save()

        # fake our version number back to 8
        flow.version_number = 8
        flow.save()

        # now make sure a call to get dependencies succeeds and shows our flow
        triggeree = Flow.objects.filter(name='Triggeree').first()
        self.assertIn(triggeree, flow.get_dependencies()['flows'])

    def test_trigger_flow(self):
        self.import_file('triggered_flow')

        flow = Flow.objects.filter(name='Trigger a Flow', org=self.org).first()
        definition = flow.as_json()
        actions = definition[Flow.ACTION_SETS][0]['actions']
        self.assertEquals(1, len(actions))
        self.assertEquals('Triggered Flow', actions[0]['flow']['name'])

    def test_trigger_dependency(self):
        # tests the case of us doing an export of only a single flow (despite dependencies) and making sure we
        # don't include the triggers of our dependent flows (which weren't exported)
        self.import_file('parent_child_trigger')

        parent = Flow.objects.filter(name='Parent Flow').first()

        self.login(self.admin)

        # export only the parent
        post_data = dict(flows=[parent.pk], campaigns=[])
        response = self.client.post(reverse('orgs.org_export'), post_data)

        exported = json.loads(response.content)

        # shouldn't have any triggers
        self.assertFalse(exported['triggers'])

    def test_subflow_dependencies(self):
        self.import_file('subflow')

        parent = Flow.objects.filter(name='Parent Flow').first()
        child = Flow.objects.filter(name='Child Flow').first()
        self.assertIn(child, parent.get_dependencies()['flows'])

        self.login(self.admin)
        response = self.client.get(reverse('orgs.org_export'))

        from BeautifulSoup import BeautifulSoup
        soup = BeautifulSoup(response.content)
        group = str(soup.findAll("div", {"class": "exportables bucket"})[0])

        self.assertIn('Parent Flow', group)
        self.assertIn('Child Flow', group)

    def test_flow_export_dynamic_group(self):
        flow = self.get_flow('favorites')

        # get one of our flow actionsets, change it to an AddToGroupAction
        actionset = ActionSet.objects.filter(flow=flow).order_by('y').first()

        # replace the actions
        from temba.flows.models import AddToGroupAction
        actionset.set_actions_dict([AddToGroupAction([dict(uuid='123', name="Other Group"), '@contact.name']).as_json()])
        actionset.save()

        # now let's export!
        self.login(self.admin)
        post_data = dict(flows=[flow.pk], campaigns=[])
        response = self.client.post(reverse('orgs.org_export'), post_data)
        exported = json.loads(response.content)

        # try to import the flow
        flow.delete()
        json.loads(response.content)
        Flow.import_flows(exported, self.org, self.admin)

        # make sure the created flow has the same action set
        flow = Flow.objects.filter(name="%s" % flow.name).first()
        actionset = ActionSet.objects.filter(flow=flow).order_by('y').first()
        self.assertTrue('@contact.name' in actionset.get_actions()[0].groups)

    def test_missing_flows_on_import(self):
        # import a flow that starts a missing flow
        self.import_file('start_missing_flow')

        # the flow that kicks off our missing flow
        flow = Flow.objects.get(name='Start Missing Flow')

        # make sure our missing flow is indeed not there
        self.assertIsNone(Flow.objects.filter(name='Missing Flow').first())

        # these two actionsets only have a single action that starts the missing flow
        # therefore they should not be created on import
        self.assertIsNone(ActionSet.objects.filter(flow=flow, y=160, x=90).first())
        self.assertIsNone(ActionSet.objects.filter(flow=flow, y=233, x=395).first())

        # should have this actionset, but only one action now since one was removed
        other_actionset = ActionSet.objects.filter(flow=flow, y=145, x=731).first()
        self.assertEquals(1, len(other_actionset.get_actions()))

        # now make sure it does the same thing from an actionset
        self.import_file('start_missing_flow_from_actionset')
        self.assertIsNotNone(Flow.objects.filter(name='Start Missing Flow').first())
        self.assertIsNone(Flow.objects.filter(name='Missing Flow').first())

    def test_import(self):

        self.login(self.admin)

        # try importing without having purchased credits
        post_data = dict(import_file=open('%s/test_flows/new_mother.json' % settings.MEDIA_ROOT, 'rb'))
        response = self.client.post(reverse('orgs.org_import'), post_data)
        self.assertEquals(response.context['form'].errors['import_file'][0], 'Sorry, import is a premium feature')

        # now purchase some credits and try again
        TopUp.objects.create(org=self.org, price=1, credits=10000,
                             expires_on=timezone.now() + timedelta(days=30),
                             created_by=self.admin, modified_by=self.admin)

        # force our cache to reload
        self.org.get_credits_total(force_dirty=True)
        self.org.update_caches(OrgEvent.topup_updated, None)
        self.assertTrue(self.org.get_purchased_credits() > 0)

        # now try again with purchased credits, but our file is too old
        post_data = dict(import_file=open('%s/test_flows/too_old.json' % settings.MEDIA_ROOT, 'rb'))
        response = self.client.post(reverse('orgs.org_import'), post_data)
        self.assertEquals(response.context['form'].errors['import_file'][0], 'This file is no longer valid. Please export a new version and try again.')

        # simulate an unexpected exception during import
        with patch('temba.triggers.models.Trigger.import_triggers') as validate:
            validate.side_effect = Exception('Unexpected Error')
            post_data = dict(import_file=open('%s/test_flows/new_mother.json' % settings.MEDIA_ROOT, 'rb'))
            response = self.client.post(reverse('orgs.org_import'), post_data)
            self.assertEquals(response.context['form'].errors['import_file'][0], 'Sorry, your import file is invalid.')

            # trigger import failed, new flows that were added should get rolled back
            self.assertIsNone(Flow.objects.filter(org=self.org, name='New Mother').first())

    def test_export_import(self):

        def assert_object_counts():
            self.assertEquals(8, Flow.objects.filter(org=self.org, is_active=True, is_archived=False, flow_type='F').count())
            self.assertEquals(2, Flow.objects.filter(org=self.org, is_active=True, is_archived=False, flow_type='M').count())
            self.assertEquals(1, Campaign.objects.filter(org=self.org, is_archived=False).count())
            self.assertEquals(4, CampaignEvent.objects.filter(campaign__org=self.org, event_type='F').count())
            self.assertEquals(2, CampaignEvent.objects.filter(campaign__org=self.org, event_type='M').count())
            self.assertEquals(2, Trigger.objects.filter(org=self.org, trigger_type='K', is_archived=False).count())
            self.assertEquals(1, Trigger.objects.filter(org=self.org, trigger_type='C', is_archived=False).count())
            self.assertEquals(1, Trigger.objects.filter(org=self.org, trigger_type='M', is_archived=False).count())
            self.assertEquals(3, ContactGroup.user_groups.filter(org=self.org).count())
            self.assertEquals(1, Label.label_objects.filter(org=self.org).count())

        # import all our bits
        self.import_file('the_clinic')

        # check that the right number of objects successfully imported for our app
        assert_object_counts()

        # let's update some stuff
        confirm_appointment = Flow.objects.get(name='Confirm Appointment')
        confirm_appointment.expires_after_minutes = 60
        confirm_appointment.save()

        action_set = confirm_appointment.action_sets.order_by('-y').first()
        actions = action_set.get_actions_dict()
        actions[0]['msg']['base'] = 'Thanks for nothing'
        action_set.set_actions_dict(actions)
        action_set.save()

        trigger = Trigger.objects.filter(keyword='patient').first()
        trigger.flow = confirm_appointment
        trigger.save()

        message_flow = Flow.objects.filter(flow_type='M').order_by('pk').first()
        action_set = message_flow.action_sets.order_by('-y').first()
        actions = action_set.get_actions_dict()
        self.assertEquals("Hi there, just a quick reminder that you have an appointment at The Clinic at @contact.next_appointment. If you can't make it please call 1-888-THE-CLINIC.", actions[0]['msg']['base'])
        actions[0]['msg'] = 'No reminders for you!'
        action_set.set_actions_dict(actions)
        action_set.save()

        # now reimport
        self.import_file('the_clinic')

        # our flow should get reset from the import
        confirm_appointment = Flow.objects.get(pk=confirm_appointment.pk)
        action_set = confirm_appointment.action_sets.order_by('-y').first()
        actions = action_set.get_actions_dict()
        self.assertEquals("Thanks, your appointment at The Clinic has been confirmed for @contact.next_appointment. See you then!", actions[0]['msg']['base'])

        # same with our trigger
        trigger = Trigger.objects.filter(keyword='patient').first()
        self.assertEquals(Flow.objects.filter(name='Register Patient').first(), trigger.flow)

        # our old campaign message flow should be inactive now
        self.assertTrue(Flow.objects.filter(pk=message_flow.pk, is_active=False))

        # find our new message flow, and see that the original message is there
        message_flow = Flow.objects.filter(flow_type='M', is_active=True).order_by('pk').first()
        action_set = Flow.objects.get(pk=message_flow.pk).action_sets.order_by('-y').first()
        actions = action_set.get_actions_dict()
        self.assertEquals("Hi there, just a quick reminder that you have an appointment at The Clinic at @contact.next_appointment. If you can't make it please call 1-888-THE-CLINIC.", actions[0]['msg']['base'])

        # and we should have the same number of items as after the first import
        assert_object_counts()

        # see that everything shows up properly on our export page
        self.login(self.admin)
        response = self.client.get(reverse('orgs.org_export'))
        self.assertContains(response, 'Register Patient')
        self.assertContains(response, 'Catch All')
        self.assertContains(response, 'Missed Call')
        self.assertContains(response, 'Start Notifications')
        self.assertContains(response, 'Stop Notifications')
        self.assertContains(response, 'Confirm Appointment')
        self.assertContains(response, 'Appointment Followup')

        # our campaign
        self.assertContains(response, 'Appointment Schedule')

        # now let's export!
        post_data = dict(flows=[f.pk for f in Flow.objects.filter(flow_type='F')],
                         campaigns=[c.pk for c in Campaign.objects.all()])

        response = self.client.post(reverse('orgs.org_export'), post_data)
        exported = json.loads(response.content)
        self.assertEquals(CURRENT_EXPORT_VERSION, exported.get('version', 0))
        self.assertEquals('https://app.rapidpro.io', exported.get('site', None))

        self.assertEquals(8, len(exported.get('flows', [])))
        self.assertEquals(4, len(exported.get('triggers', [])))
        self.assertEquals(1, len(exported.get('campaigns', [])))

        # finally let's try importing our exported file
        self.org.import_app(exported, self.admin, site='http://app.rapidpro.io')
        assert_object_counts()

        # let's rename a flow and import our export again
        flow = Flow.objects.get(name='Confirm Appointment')
        flow.name = "A new flow"
        flow.save()

        campaign = Campaign.objects.all().first()
        campaign.name = "A new campagin"
        campaign.save()

        group = ContactGroup.user_groups.filter(name='Pending Appointments').first()
        group.name = "A new group"
        group.save()

        # it should fall back on ids and not create new objects even though the names changed
        self.org.import_app(exported, self.admin, site='http://app.rapidpro.io')
        assert_object_counts()

        # and our objets should have the same names as before
        self.assertEquals('Confirm Appointment', Flow.objects.get(pk=flow.pk).name)
        self.assertEquals('Appointment Schedule', Campaign.objects.all().first().name)
        self.assertEquals('Pending Appointments', ContactGroup.user_groups.get(pk=group.pk).name)

        # let's rename our objects again
        flow.name = "A new name"
        flow.save()

        campaign.name = "A new campagin"
        campaign.save()

        group.name = "A new group"
        group.save()

        # now import the same import but pretend its from a different site
        self.org.import_app(exported, self.admin, site='http://temba.io')

        # the newly named objects won't get updated in this case and we'll create new ones instead
        self.assertEquals(9, Flow.objects.filter(org=self.org, is_archived=False, flow_type='F').count())
        self.assertEquals(2, Campaign.objects.filter(org=self.org, is_archived=False).count())
        self.assertEquals(4, ContactGroup.user_groups.filter(org=self.org).count())

        # now archive a flow
        register = Flow.objects.filter(name='Register Patient').first()
        register.is_archived = True
        register.save()

        # default view shouldn't show archived flows
        response = self.client.get(reverse('orgs.org_export'))
        self.assertNotContains(response, 'Register Patient')

        # with the archived flag one, it should be there
        response = self.client.get("%s?archived=1" % reverse('orgs.org_export'))
        self.assertContains(response, 'Register Patient')

        # delete our flow, and reimport
        confirm_appointment.delete()
        self.org.import_app(exported, self.admin, site='https://app.rapidpro.io')

        # make sure we have the previously exported expiration
        confirm_appointment = Flow.objects.get(name='Confirm Appointment')
        self.assertEquals(60, confirm_appointment.expires_after_minutes)

        # now delete a flow
        register = Flow.objects.filter(name='Register Patient').first()
        register.is_active = False
        register.save()

        # default view shouldn't show deleted flows
        response = self.client.get(reverse('orgs.org_export'))
        self.assertNotContains(response, 'Register Patient')

        # even with the archived flag one deleted flows should not show up
        response = self.client.get("%s?archived=1" % reverse('orgs.org_export'))
        self.assertNotContains(response, 'Register Patient')


class CreditAlertTest(TembaTest):
    def test_check_org_credits(self):
        self.joe = self.create_contact("Joe Blow", "123")
        self.create_msg(contact=self.joe)
        with self.settings(HOSTNAME="rapidpro.io", SEND_EMAILS=True):
            with patch('temba.orgs.models.Org.get_credits_remaining') as mock_get_credits_remaining:
                mock_get_credits_remaining.return_value = -1

                # no alert yet
                self.assertFalse(CreditAlert.objects.all())

                CreditAlert.check_org_credits()

                # one alert created and sent
                self.assertEquals(1, CreditAlert.objects.filter(is_active=True, org=self.org,
                                                                alert_type=ORG_CREDIT_OVER).count())
                self.assertEquals(1, len(mail.outbox))

                # alert email is for out of credits type
                sent_email = mail.outbox[0]
                self.assertEqual(len(sent_email.to), 1)
                self.assertTrue('RapidPro account for Temba' in sent_email.body)
                self.assertTrue('is out of credit.' in sent_email.body)

                # no new alert if one is sent and no new email
                CreditAlert.check_org_credits()
                self.assertEquals(1, CreditAlert.objects.filter(is_active=True, org=self.org,
                                                                alert_type=ORG_CREDIT_OVER).count())
                self.assertEquals(1, len(mail.outbox))

                # reset alerts
                CreditAlert.reset_for_org(self.org)
                self.assertFalse(CreditAlert.objects.filter(org=self.org, is_active=True))

                # can resend a new alert
                CreditAlert.check_org_credits()
                self.assertEquals(1, CreditAlert.objects.filter(is_active=True, org=self.org,
                                                                alert_type=ORG_CREDIT_OVER).count())
                self.assertEquals(2, len(mail.outbox))

                mock_get_credits_remaining.return_value = 10

                with patch('temba.orgs.models.Org.has_low_credits') as mock_has_low_credits:
                    mock_has_low_credits.return_value = True

                    self.assertFalse(CreditAlert.objects.filter(org=self.org, alert_type=ORG_CREDIT_LOW))

                    CreditAlert.check_org_credits()

                    # low credit alert created and email sent
                    self.assertEquals(1, CreditAlert.objects.filter(is_active=True, org=self.org,
                                                                    alert_type=ORG_CREDIT_LOW).count())
                    self.assertEquals(3, len(mail.outbox))

                    # email sent
                    sent_email = mail.outbox[2]
                    self.assertEqual(len(sent_email.to), 1)
                    self.assertTrue('RapidPro account for Temba' in sent_email.body)
                    self.assertTrue('is running low on credits' in sent_email.body)

                    # no new alert if one is sent and no new email
                    CreditAlert.check_org_credits()
                    self.assertEquals(1, CreditAlert.objects.filter(is_active=True, org=self.org,
                                                                    alert_type=ORG_CREDIT_LOW).count())
                    self.assertEquals(3, len(mail.outbox))

                    # reset alerts
                    CreditAlert.reset_for_org(self.org)
                    self.assertFalse(CreditAlert.objects.filter(org=self.org, is_active=True))

                    # can resend a new alert
                    CreditAlert.check_org_credits()
                    self.assertEquals(1, CreditAlert.objects.filter(is_active=True, org=self.org,
                                                                    alert_type=ORG_CREDIT_LOW).count())
                    self.assertEquals(4, len(mail.outbox))

                    mock_has_low_credits.return_value = False

                    with patch('temba.orgs.models.Org.get_credits_expiring_soon') as mock_get_credits_exipiring_soon:
                        mock_get_credits_exipiring_soon.return_value = 0

                        self.assertFalse(CreditAlert.objects.filter(org=self.org, alert_type=ORG_CREDIT_EXPIRING))

                        CreditAlert.check_org_credits()

                        # no alert since no expiring credits
                        self.assertFalse(CreditAlert.objects.filter(org=self.org, alert_type=ORG_CREDIT_EXPIRING))

                        mock_get_credits_exipiring_soon.return_value = 200

                        CreditAlert.check_org_credits()

                        # expiring credit alert created and email sent
                        self.assertEquals(1, CreditAlert.objects.filter(is_active=True, org=self.org,
                                                                        alert_type=ORG_CREDIT_EXPIRING).count())
                        self.assertEquals(5, len(mail.outbox))

                        # email sent
                        sent_email = mail.outbox[4]
                        self.assertEqual(len(sent_email.to), 1)
                        self.assertTrue('RapidPro account for Temba' in sent_email.body)
                        self.assertTrue('expiring credits in less than one month.' in sent_email.body)

                        # no new alert if one is sent and no new email
                        CreditAlert.check_org_credits()
                        self.assertEquals(1, CreditAlert.objects.filter(is_active=True, org=self.org,
                                                                        alert_type=ORG_CREDIT_EXPIRING).count())
                        self.assertEquals(5, len(mail.outbox))

                        # reset alerts
                        CreditAlert.reset_for_org(self.org)
                        self.assertFalse(CreditAlert.objects.filter(org=self.org, is_active=True))

                        # can resend a new alert
                        CreditAlert.check_org_credits()
                        self.assertEquals(1, CreditAlert.objects.filter(is_active=True, org=self.org,
                                                                        alert_type=ORG_CREDIT_EXPIRING).count())
                        self.assertEquals(6, len(mail.outbox))


class UnreadCountTest(FlowFileTest):

    def test_unread_count_test(self):
        flow = self.get_flow('favorites')

        # create a trigger for 'favs'
        Trigger.objects.create(org=self.org, flow=flow, keyword='favs', created_by=self.admin, modified_by=self.admin)

        # start our flow by firing an incoming message
        contact = self.create_contact('Anakin Skywalker', '+12067791212')
        msg = self.create_msg(contact=contact, text="favs")

        # process it
        Msg.process_message(msg)

        # our flow unread count should have gone up
        self.assertEquals(1, flow.get_and_clear_unread_responses())

        # cleared by the first call
        self.assertEquals(0, flow.get_and_clear_unread_responses())

        # at this point our flow should have started.. go to our trigger list page to see if our context is correct
        self.login(self.admin)
        trigger_list = reverse('triggers.trigger_list')
        response = self.client.get(trigger_list)

        self.assertEquals(0, response.context['msgs_unread_count'])
        self.assertEquals(1, response.context['flows_unread_count'])

        # answer another question in the flow
        msg = self.create_msg(contact=contact, text="red")
        Msg.process_message(msg)

        response = self.client.get(trigger_list)
        self.assertEquals(0, response.context['msgs_unread_count'])
        self.assertEquals(2, response.context['flows_unread_count'])

        # finish the flow and send a message outside it
        msg = self.create_msg(contact=contact, text="primus")
        Msg.process_message(msg)

        msg = self.create_msg(contact=contact, text="nic")
        Msg.process_message(msg)

        msg = self.create_msg(contact=contact, text="Hello?")
        Msg.process_message(msg)

        response = self.client.get(trigger_list)
        self.assertEquals(4, response.context['flows_unread_count'])
        self.assertEquals(1, response.context['msgs_unread_count'])

        # visit the msg pane
        response = self.client.get(reverse('msgs.msg_inbox'))
        self.assertEquals(4, response.context['flows_unread_count'])
        self.assertEquals(0, response.context['msgs_unread_count'])

        # now the flow list pane
        response = self.client.get(reverse('flows.flow_list'))
        self.assertEquals(0, response.context['flows_unread_count'])
        self.assertEquals(0, response.context['msgs_unread_count'])

        # make sure a test contact doesn't update our counts
        test_contact = self.create_contact("Test Contact", "+12065551214", is_test=True)

        msg = self.create_msg(contact=test_contact, text="favs")
        Msg.process_message(msg)

        # assert our counts weren't updated
        self.assertEquals(0, self.org.get_unread_msg_count(UNREAD_INBOX_MSGS))
        self.assertEquals(0, self.org.get_unread_msg_count(UNREAD_FLOW_MSGS))

        # wasn't counted for the individual flow
        self.assertEquals(0, flow.get_and_clear_unread_responses())


class EmailContextProcessorsTest(SmartminTest):
    def setUp(self):
        super(EmailContextProcessorsTest, self).setUp()
        self.admin = self.create_user("Administrator")
        self.middleware = BrandingMiddleware()

    def test_link_components(self):
        self.request = Mock(spec=HttpRequest)
        self.request.get_host.return_value = "rapidpro.io"
        response = self.middleware.process_request(self.request)
        self.assertIsNone(response)
        self.assertEquals(link_components(self.request, self.admin), dict(protocol="https", hostname="app.rapidpro.io"))

        with self.settings(HOSTNAME="rapidpro.io"):
            forget_url = reverse('users.user_forget')

            post_data = dict()
            post_data['email'] = 'nouser@nouser.com'

            response = self.client.post(forget_url, post_data, follow=True)
            self.assertEquals(1, len(mail.outbox))
            sent_email = mail.outbox[0]
            self.assertEqual(len(sent_email.to), 1)
            self.assertEqual(sent_email.to[0], 'nouser@nouser.com')

            # we have the domain of rapipro.io brand
            self.assertTrue('app.rapidpro.io' in sent_email.body)


class TestStripeCredits(TembaTest):

    @patch('stripe.Customer.create')
    @patch('stripe.Charge.create')
    @override_settings(SEND_EMAILS=True)
    def test_add_credits(self, charge_create, customer_create):
        customer_create.return_value = dict_to_struct('Customer', dict(id='stripe-cust-1'))
        charge_create.return_value = \
            dict_to_struct('Charge', dict(id='stripe-charge-1',
                                          card=dict_to_struct('Card', dict(last4='1234', type='Visa', name='Rudolph'))))

        settings.BRANDING[settings.DEFAULT_BRAND]['bundles'] = (dict(cents="2000", credits=1000, feature=""),)

        self.org.add_credits('2000', 'stripe-token', self.admin)
        self.assertTrue(2000, self.org.get_credits_total())

        # assert we saved our charge info
        topup = self.org.topups.last()
        self.assertEqual('stripe-charge-1', topup.stripe_charge)

        # and we saved our stripe customer info
        org = Org.objects.get(id=self.org.id)
        self.assertEqual('stripe-cust-1', org.stripe_customer)

        # assert we sent our confirmation emai
        self.assertEqual(1, len(mail.outbox))
        email = mail.outbox[0]
        self.assertEquals("RapidPro Receipt", email.subject)
        self.assertTrue('Rudolph' in email.body)
        self.assertTrue('Visa' in email.body)
        self.assertTrue('$20' in email.body)

    @patch('stripe.Customer.create')
    def test_add_credits_fail(self, customer_create):
        customer_create.side_effect = ValueError("Invalid customer token")

        with self.assertRaises(ValidationError):
            self.org.add_credits('2000', 'stripe-token', self.admin)

        # assert no email was sent
        self.assertEqual(0, len(mail.outbox))

        # and no topups created
        self.assertEqual(1, self.org.topups.all().count())
        self.assertEqual(1000, self.org.get_credits_total())

    def test_add_credits_invalid_bundle(self):

        with self.assertRaises(ValidationError):
            self.org.add_credits('-10', 'stripe-token', self.admin)

        # assert no email was sent
        self.assertEqual(0, len(mail.outbox))

        # and no topups created
        self.assertEqual(1, self.org.topups.all().count())
        self.assertEqual(1000, self.org.get_credits_total())

    @patch('stripe.Customer.retrieve')
    @patch('stripe.Charge.create')
    @override_settings(SEND_EMAILS=True)
    def test_add_credits_existing_customer(self, charge_create, customer_retrieve):
        self.org.stripe_customer = 'stripe-cust-1'
        self.org.save()

        class MockCard(object):
            def __init__(self):
                self.id = 'stripe-card-1'

            def delete(self):
                pass

        class MockCards(object):
            def all(self):
                return dict_to_struct('MockCardData', dict(data=[MockCard(), MockCard()]))

            def create(self, card):
                return MockCard()

        class MockCustomer(object):
            def __init__(self):
                self.id = 'stripe-cust-1'
                self.cards = MockCards()

            def save(self):
                pass

        customer_retrieve.return_value = MockCustomer()
        charge_create.return_value = \
            dict_to_struct('Charge', dict(id='stripe-charge-1',
                                          card=dict_to_struct('Card', dict(last4='1234', type='Visa', name='Rudolph'))))

        settings.BRANDING[settings.DEFAULT_BRAND]['bundles'] = (dict(cents="2000", credits=1000, feature=""),)

        self.org.add_credits('2000', 'stripe-token', self.admin)
        self.assertTrue(2000, self.org.get_credits_total())

        # assert we saved our charge info
        topup = self.org.topups.last()
        self.assertEqual('stripe-charge-1', topup.stripe_charge)

        # and we saved our stripe customer info
        org = Org.objects.get(id=self.org.id)
        self.assertEqual('stripe-cust-1', org.stripe_customer)

        # assert we sent our confirmation emai
        self.assertEqual(1, len(mail.outbox))
        email = mail.outbox[0]
        self.assertEquals("RapidPro Receipt", email.subject)
        self.assertTrue('Rudolph' in email.body)
        self.assertTrue('Visa' in email.body)
        self.assertTrue('$20' in email.body)


class ParsingTest(TembaTest):

    def test_parse_decimal(self):
        self.assertEqual(self.org.parse_decimal("Not num"), None)
        self.assertEqual(self.org.parse_decimal("00.123"), Decimal("0.123"))
        self.assertEqual(self.org.parse_decimal("6e33"), None)
        self.assertEqual(self.org.parse_decimal("6e5"), Decimal("600000"))
        self.assertEqual(self.org.parse_decimal("9999999999999999999999999"), None)
        self.assertEqual(self.org.parse_decimal(""), None)
        self.assertEqual(self.org.parse_decimal("NaN"), None)
        self.assertEqual(self.org.parse_decimal("Infinity"), None)
