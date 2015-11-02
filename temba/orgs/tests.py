from __future__ import unicode_literals

import json

from context_processors import GroupPermWrapper
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core import mail
from django.core.urlresolvers import reverse
from django.http import HttpRequest
from django.test.utils import override_settings
from django.utils import timezone
from mock import patch, Mock
from smartmin.tests import SmartminTest
from temba.campaigns.models import Campaign, CampaignEvent
from temba.contacts.models import Contact, ContactGroup, TEL_SCHEME, TWITTER_SCHEME
from temba.middleware import BrandingMiddleware
from temba.channels.models import Channel, RECEIVE, SEND, TWILIO, TWITTER, PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN
from temba.flows.models import Flow, ActionSet
from temba.msgs.models import Label, Msg, INCOMING
from temba.utils.email import link_components
from temba.tests import TembaTest, MockResponse, MockTwilioClient, MockRequestValidator, FlowFileTest
from temba.triggers.models import Trigger
from .models import Org, OrgEvent, TopUp, Invitation, Language, DAYFIRST, MONTHFIRST, CURRENT_EXPORT_VERSION
from .models import UNREAD_FLOW_MSGS, UNREAD_INBOX_MSGS


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
        from temba.locations.models import AdminBoundary
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

        # change to the trial plan
        response = self.client.post(update_url, post_data)
        self.assertEquals(302, response.status_code)

    @override_settings(SEND_EMAILS=True)
    def test_manage_accounts(self):
        manage_accounts_url = reverse('orgs.org_manage_accounts')

        self.login(self.admin)

        response = self.client.get(manage_accounts_url)
        self.assertEquals(200, response.status_code)

        # we have 19 fields in the form including 16 checkboxes for the four users, an email field, a user group field
        # and 'loc' field.
        self.assertEquals(19, len(response.context['form'].fields))
        self.assertTrue('emails' in response.context['form'].fields)
        self.assertTrue('user_group' in response.context['form'].fields)
        for user in [self.user, self.editor, self.admin]:
            self.assertTrue("administrators_%d" % user.pk in response.context['form'].fields)
            self.assertTrue("editors_%d" % user.pk in response.context['form'].fields)
            self.assertTrue("viewers_%d" % user.pk in response.context['form'].fields)
            self.assertTrue("surveyors_%d" % user.pk in response.context['form'].fields)

        self.assertFalse(response.context['form'].fields['emails'].initial)
        self.assertEquals('V', response.context['form'].fields['user_group'].initial)

        # keep admin as admin, editor as editor, but make user an editor too
        post_data = {
            'administrators_%d' % self.admin.pk: 'on',
            'editors_%d' % self.editor.pk: 'on',
            'editors_%d' % self.user.pk: 'on',
            'user_group': 'E'
        }
        response = self.client.post(manage_accounts_url, post_data)
        self.assertEquals(302, response.status_code)

        org = Org.objects.get(pk=self.org.pk)
        self.assertEqual(set(org.administrators.all()), {self.admin})
        self.assertEqual(set(org.editors.all()), {self.user, self.editor})
        self.assertFalse(set(org.viewers.all()), set())

        # add to post_data an email to invite as admin
        post_data['emails'] = "norkans7gmail.com"
        post_data['user_group'] = 'A'
        response = self.client.post(manage_accounts_url, post_data)
        self.assertTrue('emails' in response.context['form'].errors)
        self.assertEquals("One of the emails you entered is invalid.", response.context['form'].errors['emails'][0])

        # now post with right email
        post_data['emails'] = "norkans7@gmail.com"
        post_data['user_group'] = 'A'
        response = self.client.post(manage_accounts_url, post_data)

        # an invitation is created and sent by email
        self.assertEquals(1, Invitation.objects.all().count())
        self.assertTrue(len(mail.outbox) == 1)

        invitation = Invitation.objects.get()

        self.assertEquals(invitation.org, self.org)
        self.assertEquals(invitation.email, "norkans7@gmail.com")
        self.assertEquals(invitation.user_group, "A")

        # pretend our invite was acted on
        Invitation.objects.all().update(is_active=False)

        # send another invitation, different group
        post_data['emails'] = "norkans7@gmail.com"
        post_data['user_group'] = 'E'
        self.client.post(manage_accounts_url, post_data)

        # old invite should be updated
        new_invite = Invitation.objects.all().first()
        self.assertEquals(1, Invitation.objects.all().count())
        self.assertEquals(invitation.pk, new_invite.pk)
        self.assertEquals('E', new_invite.user_group)
        self.assertEquals(2, len(mail.outbox))
        self.assertTrue(new_invite.is_active)

        # post many emails to the form
        post_data['emails'] = "norbert@temba.com,code@temba.com"
        post_data['user_group'] = 'A'
        self.client.post(manage_accounts_url, post_data)

        # now 2 new invitations are created and sent
        self.assertEquals(3, Invitation.objects.all().count())
        self.assertEquals(4, len(mail.outbox))

        # Update our users, making the 'user' user a surveyor
        post_data = {
            'administrators_%d' % self.admin.pk: 'on',
            'editors_%d' % self.editor.pk: 'on',
            'surveyors_%d' % self.user.pk: 'on',
            'user_group': 'E'
        }

        # successful post redirects
        response = self.client.post(manage_accounts_url, post_data)
        self.assertEquals(302, response.status_code)

        org = Org.objects.get(pk=self.org.pk)
        self.assertEqual(set(org.administrators.all()), {self.admin})
        self.assertEqual(set(org.editors.all()), {self.editor})
        self.assertEqual(set(org.surveyors.all()), {self.user})

        # upgrade one of our users to an admin
        self.org.editors.remove(self.user)
        self.org.administrators.add(self.user)

        # now remove ourselves as an admin
        post_data = {
            'administrators_%d' % self.user.pk: 'on',
            'editors_%d' % self.editor.pk: 'on',
            'user_group': 'E'
        }

        response = self.client.post(manage_accounts_url, post_data)

        # should be redirected to chooser page
        self.assertRedirect(response, reverse('orgs.org_choose'))

        # and should no longer be an admin
        self.assertFalse(self.admin in self.org.administrators.all())

    @patch('temba.utils.email.send_temba_email')
    def test_join(self, mock_send_temba_email):
        editor_invitation = Invitation.objects.create(org=self.org,
                                                      user_group="E",
                                                      email="norkans7@gmail.com",
                                                      host='app.rapidpro.io',
                                                      created_by=self.admin,
                                                      modified_by=self.admin)

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

        # a non org user get a message to contact their administrator
        self.login(self.non_org_user)
        response = self.client.get(choose_url)
        self.assertEquals(200, response.status_code)
        self.assertEquals(0, len(response.context['orgs']))
        self.assertContains(response, "Your account is not associated with any organization. Please contact your administrator to receive an invitation to an organization.")

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

    def test_topups(self):
        contact = self.create_contact("Michael Shumaucker", "+250788123123")
        test_contact = Contact.get_test_contact(self.user)
        welcome_topup = TopUp.objects.get()

        def create_msgs(recipient, count):
            for m in range(count):
                self.create_msg(contact=recipient, direction='I', text="Test %d" % m)

        create_msgs(contact, 10)

        # we should have 1000 minus 10 credits for this org
        with self.assertNumQueries(4):
            self.assertEquals(990, self.org.get_credits_remaining())  # from db

        with self.assertNumQueries(0):
            self.assertEquals(1000, self.org.get_credits_total())  # from cache
            self.assertEquals(10, self.org.get_credits_used())
            self.assertEquals(990, self.org.get_credits_remaining())

        self.assertEquals(10, welcome_topup.msgs.count())
        self.assertEquals(10, TopUp.objects.get(pk=welcome_topup.pk).get_used())

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

        # test pro user status
        self.assertFalse(self.org.is_pro())

        # add new topup with lots of credits
        mega_topup = TopUp.create(self.admin, price=0, credits=100000)
        self.org.update_caches(OrgEvent.topup_updated, None)

        # after applying this, no non-test messages should be without a topup
        self.org.apply_topups()
        self.assertFalse(Msg.objects.filter(org=self.org, contact__is_test=False, topup=None))
        self.assertFalse(Msg.objects.filter(org=self.org, contact__is_test=True).exclude(topup=None))
        self.assertEquals(5, TopUp.objects.get(pk=mega_topup.pk).get_used())

        # now we're pro
        self.assertTrue(self.org.is_pro())
        self.assertEquals(100025, self.org.get_credits_total())
        self.assertEquals(100025, self.org.get_purchased_credits())
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

        # we're still pro though
        self.assertTrue(self.org.is_pro())

        with self.assertNumQueries(2):
            self.assertEquals(100025, self.org.get_purchased_credits())
            self.assertEquals(31, self.org.get_credits_total())
            self.assertEquals(32, self.org.get_credits_used())
            self.assertEquals(-1, self.org.get_credits_remaining())

    @patch('temba.orgs.views.TwilioRestClient', MockTwilioClient)
    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_twilio_connect(self):
        connect_url = reverse("orgs.org_twilio_connect")

        self.login(self.admin)
        self.admin.set_org(self.org)

        response = self.client.get(connect_url)
        self.assertEquals(200, response.status_code)
        self.assertTrue(response.context['form'])
        self.assertEquals(len(response.context['form'].fields.keys()), 3)
        self.assertIn('account_sid', response.context['form'].fields.keys())
        self.assertIn('account_token', response.context['form'].fields.keys())

        with patch('temba.tests.MockTwilioClient.MockAccounts.get') as mock_get:
            with patch('temba.tests.MockTwilioClient.MockApplications.list') as mock_apps_list:
                mock_get.return_value = MockTwilioClient.MockAccount('Full')
                mock_apps_list.return_value = [MockTwilioClient.MockApplication("%s/%d" % (settings.TEMBA_HOST.lower(),
                                                                                           self.org.pk))]

                post_data = dict()
                post_data['account_sid'] = "AccountSid"
                post_data['account_token'] = "AccountToken"

                response = self.client.post(connect_url, post_data)

                org = Org.objects.get(pk=self.org.pk)
                self.assertEquals(org.config_json()['ACCOUNT_SID'], "AccountSid")
                self.assertEquals(org.config_json()['ACCOUNT_TOKEN'], "AccountToken")
                self.assertTrue(org.config_json()['APPLICATION_SID'])

                # when the user submit the secondary token, we use it to get the primary one from the rest API
                with patch('temba.tests.MockTwilioClient.MockAccounts.get') as mock_get_primary:
                    mock_get_primary.return_value = MockTwilioClient.MockAccount('Full', 'PrimaryAccountToken')

                    response = self.client.post(connect_url, post_data)

                    org = Org.objects.get(pk=self.org.pk)
                    self.assertEquals(org.config_json()['ACCOUNT_SID'], "AccountSid")
                    self.assertEquals(org.config_json()['ACCOUNT_TOKEN'], "PrimaryAccountToken")
                    self.assertTrue(org.config_json()['APPLICATION_SID'])

        twilio_account_url = reverse('orgs.org_twilio_account')
        response = self.client.get(twilio_account_url)
        self.assertEquals("AccountSid", response.context['config']['ACCOUNT_SID'])

        response = self.client.post(twilio_account_url, dict(), follow=True)
        org = Org.objects.get(pk=self.org.pk)
        self.assertEquals(org.config_json()['ACCOUNT_SID'],"" )
        self.assertEquals(org.config_json()['ACCOUNT_TOKEN'], "")
        self.assertEquals(org.config_json()['APPLICATION_SID'], "")

        # we should not update any other field
        response = self.client.post(twilio_account_url, dict(name="DO NOT CHANGE ME"), follow=True)
        org = Org.objects.get(pk=self.org.pk)
        self.assertEquals(org.name, "Temba")

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
        self.org.remove_nexmo_account()
        self.assertFalse(self.org.is_connected_to_nexmo())
        self.assertFalse(self.org.config_json()['NEXMO_KEY'])
        self.assertFalse(self.org.config_json()['NEXMO_SECRET'])

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
            self.assertFalse(PLIVO_AUTH_ID in self.client.session)
            self.assertFalse(PLIVO_AUTH_TOKEN in self.client.session)

        # ok, now with a success
        with patch('requests.get') as plivo_mock:
            plivo_mock.return_value = MockResponse(200, json.dumps(dict()))
            self.client.post(connect_url, dict(auth_id='auth-id', auth_token='auth-token'))

            # plivo should be added to the session
            self.assertEquals(self.client.session[PLIVO_AUTH_ID], 'auth-id')
            self.assertEquals(self.client.session[PLIVO_AUTH_TOKEN], 'auth-token')

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
        self.assertEquals(1, Msg.objects.all().count())

        # shouldn't show the number on the outgoing page
        response = self.client.get(reverse('msgs.msg_outbox'))

        self.assertNotContains(response, "788 123 123")

        # also shouldn't show up on the flow results page
        response = self.client.get(reverse('flows.flow_results', args=[flow.pk]) + "?json=true")
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, masked)

        # create an incoming SMS, check our flow page
        Msg.create_incoming(self.channel, (TEL_SCHEME, contact.get_urn().path), "Blue")
        response = self.client.get(reverse('msgs.msg_flow'))
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, masked)

        # send another, this will be in our inbox this time
        Msg.create_incoming(self.channel, (TEL_SCHEME, contact.get_urn().path), "Where's the beef?")
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

        user = User.objects.get(username="john@carmack.com")
        self.assertTrue(org.administrators.filter(username="john@carmack.com"))
        self.assertTrue(org.administrators.filter(username="tito"))

        # try a new org with a user that already exists instead
        del post_data['password']
        post_data['name'] = "id Software"

        response = self.client.post(grant_url, post_data, follow=True)

        self.assertContains(response, "created")

        org = Org.objects.get(name="id Software")
        self.assertEquals(100000, org.get_credits_remaining())

        user = User.objects.get(username="john@carmack.com")
        self.assertTrue(org.administrators.filter(username="john@carmack.com"))
        self.assertTrue(org.administrators.filter(username="tito"))

    def test_org_signup(self):
        signup_url = reverse('orgs.org_signup')
        response = self.client.get(signup_url)
        self.assertEquals(200, response.status_code)
        self.assertTrue('name' in response.context['form'].fields)

        # firstname and lastname are required and bad email
        post_data = dict(email="bad_email", password="HelloWorld1", name="Your Face")
        response = self.client.post(signup_url, post_data)
        self.assertTrue('first_name' in response.context['form'].errors)
        self.assertTrue('last_name' in response.context['form'].errors)
        self.assertTrue('email' in response.context['form'].errors)

        post_data = dict(first_name="Eugene", last_name="Rwagasore", email="myal@relieves.org",
                         password="badpass", name="Your Face")
        response = self.client.post(signup_url, post_data)
        self.assertTrue('password' in response.context['form'].errors)

        post_data = dict(first_name="Eugene", last_name="Rwagasore", email="myal@relieves.org",
                         password="HelloWorld1", name="Relieves World")
        response = self.client.post(signup_url, post_data)
        self.assertTrue('timezone' in response.context['form'].errors)

        post_data = dict(first_name="Eugene", last_name="Rwagasore", email="myal@relieves.org",
                         password="HelloWorld1", name="Relieves World", timezone="Africa/Kigali")
        response = self.client.post(signup_url, post_data)

        # should have a user
        user = User.objects.get(username="myal@relieves.org")
        self.assertTrue(user.check_password("HelloWorld1"))

        # user should be able to get a token
        self.assertTrue(user.api_token)

        # should have an org
        org = Org.objects.get(name="Relieves World")
        self.assertTrue(org.administrators.filter(pk=user.id))
        self.assertEquals("Relieves World", str(org))
        self.assertEquals(org.slug, "relieves-world")

        # should have 1000 credits
        self.assertEquals(1000, org.get_credits_remaining())

        # a single topup
        topup = TopUp.objects.get(org=org)
        self.assertEquals(1000, topup.credits)
        self.assertEquals(0, topup.price)

        # and user should be an administrator on that org
        self.assertTrue(org.get_org_admins().filter(pk=user.pk))

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
        self.client.login(username='myal@relieves.org', password='HelloWorld1')
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

        billg = User.objects.create(username='bill@msn.com', email='bill@msn.com')

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

        Msg.create_incoming(self.channel, (TEL_SCHEME, "250788382382"), "My name is Frank")

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

        self.assertEqual(set(), self.org.get_schemes(SEND))
        self.assertEqual(set(), self.org.get_schemes(RECEIVE))

        # add a receive only tel channel
        Channel.objects.create(name="Nexmo", channel_type=TWILIO, address="0785551212", role="R", org=self.org,
                               created_by=self.user, modified_by=self.user, secret="45678", gcm_id="123")
        self.org = Org.objects.get(pk=self.org.id)
        self.assertEqual(set(), self.org.get_schemes(SEND))
        self.assertEqual({TEL_SCHEME}, self.org.get_schemes(RECEIVE))

        # add a send/receive tel channel
        Channel.objects.create(name="Twilio", channel_type=TWILIO, address="0785553434", role="SR", org=self.org,
                               created_by=self.user, modified_by=self.user, secret="56789", gcm_id="456")
        self.org = Org.objects.get(pk=self.org.id)
        self.assertEqual({TEL_SCHEME}, self.org.get_schemes(SEND))
        self.assertEqual({TEL_SCHEME}, self.org.get_schemes(RECEIVE))

        # add a twitter channel
        Channel.create(self.org, self.user, None, TWITTER, "Twitter")
        self.org = Org.objects.get(pk=self.org.id)
        self.assertEqual({TEL_SCHEME, TWITTER_SCHEME}, self.org.get_schemes(SEND))
        self.assertEqual({TEL_SCHEME, TWITTER_SCHEME}, self.org.get_schemes(RECEIVE))

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
                                                                                  __urn__tel__0='0788123123'))
        self.assertNoFormErrors(response)

        # make sure that contact's created on is our cs rep
        contact = Contact.objects.get(urns__path='+250788123123', org=self.org)
        self.assertEquals(self.csrep, contact.created_by)

        # make sure we can manage topups as well
        response = self.client.get(reverse('orgs.topup_manage') + "?org=%d" % self.org.id)
        self.assertNotRedirect(response, '/users/login/')

        # ok, now end our session
        response = self.client.post(service_url, dict())
        self.assertRedirect(response, '/org/manage/')

        # can no longer go to inbox, asked to log in
        response = self.client.get(reverse('msgs.msg_inbox'))
        self.assertRedirect(response, '/users/login/')


class LanguageTest(TembaTest):

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

    def test_trigger_flow(self):

        self.import_file('triggered-flow')

        flow = Flow.objects.filter(name='Trigger a Flow', org=self.org).first()
        definition = flow.as_json()
        actions = definition[Flow.ACTION_SETS][0]['actions']
        self.assertEquals(1, len(actions))
        self.assertEquals('Triggered Flow', actions[0]['name'])

    def test_missing_flows_on_import(self):
        # import a flow that starts a missing flow
        self.import_file('start-missing-flow')

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
        self.import_file('start-missing-flow-from-actionset')
        self.assertIsNotNone(Flow.objects.filter(name='Start Missing Flow').first())
        self.assertIsNone(Flow.objects.filter(name='Missing Flow').first())

    def test_export_import(self):

        def assert_object_counts():
            self.assertEquals(8, Flow.objects.filter(org=self.org, is_archived=False, flow_type='F').count())
            self.assertEquals(2, Flow.objects.filter(org=self.org, is_archived=False, flow_type='M').count())
            self.assertEquals(1, Campaign.objects.filter(org=self.org, is_archived=False).count())
            self.assertEquals(4, CampaignEvent.objects.filter(campaign__org=self.org, event_type='F').count())
            self.assertEquals(2, CampaignEvent.objects.filter(campaign__org=self.org, event_type='M').count())
            self.assertEquals(2, Trigger.objects.filter(org=self.org, trigger_type='K', is_archived=False).count())
            self.assertEquals(1, Trigger.objects.filter(org=self.org, trigger_type='C', is_archived=False).count())
            self.assertEquals(1, Trigger.objects.filter(org=self.org, trigger_type='M', is_archived=False).count())
            self.assertEquals(3, ContactGroup.user_groups.filter(org=self.org).count())
            self.assertEquals(1, Label.label_objects.filter(org=self.org).count())

        # import all our bits
        self.import_file('the-clinic')

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
        self.import_file('the-clinic')

        # our flow should get reset from the import
        confirm_appointment = Flow.objects.get(pk=confirm_appointment.pk)
        action_set = confirm_appointment.action_sets.order_by('-y').first()
        actions = action_set.get_actions_dict()
        self.assertEquals("Thanks, your appointment at The Clinic has been confirmed for @contact.next_appointment. See you then!", actions[0]['msg']['base'])

        # same with our trigger
        trigger = Trigger.objects.filter(keyword='patient').first()
        self.assertEquals(Flow.objects.filter(name='Register Patient').first(), trigger.flow)

        # our old campaign message flow should be gone now
        self.assertIsNone(Flow.objects.filter(pk=message_flow.pk).first())

        # find our new message flow, and see that the original message is there
        message_flow = Flow.objects.filter(flow_type='M').order_by('pk').first()
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

