from __future__ import unicode_literals

import json

from mock import patch

from context_processors import GroupPermWrapper
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core import mail
from django.core.urlresolvers import reverse
from django.test.utils import override_settings
from django.utils import timezone
from redis_cache import get_redis_connection
from temba.campaigns.models import Campaign, CampaignEvent
from temba.contacts.models import ContactGroup, TEL_SCHEME, TWITTER_SCHEME, ExportContactsTask
from temba.orgs.models import Org, OrgCache, OrgEvent, OrgFolder, TopUp, Invitation, DAYFIRST, MONTHFIRST
from temba.orgs.models import ORG_ACTIVE_TOPUP_CACHE_KEY, ORG_TOPUP_CREDITS_CACHE_KEY, ORG_TOPUP_EXPIRES_CACHE_KEY
from temba.channels.models import Channel, RECEIVE, SEND, TWILIO, TWITTER, PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_APP_ID
from temba.flows.models import Flow, ExportFlowResultsTask
from temba.msgs.models import Broadcast, Call, Label, Msg, Schedule, CALL_IN, INCOMING, ExportMessagesTask
from temba.tests import TembaTest, MockResponse
from temba.triggers.models import Trigger
from temba.utils import datetime_to_ms


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
        self.assertEquals("Rwanda", str(org.country))

        # clear it out
        response = self.client.post(country_url, dict(country=''))

        # assert it has been
        org = Org.objects.get(pk=self.org.pk)
        self.assertFalse(org.country)

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
        self.admin.set_org(self.org)

        self.org.editors.add(self.root)
        self.org.administrators.add(self.user)

        response = self.client.get(manage_accounts_url)
        self.assertEquals(200, response.status_code)

        # we have 12 fields in the form including 9 checkboxes for the three users, an emails field a user group field and 'loc' field.
        self.assertEquals(12, len(response.context['form'].fields))
        self.assertTrue('emails' in response.context['form'].fields)
        self.assertTrue('user_group' in response.context['form'].fields)
        for user in [self.root, self.user, self.admin]:
            self.assertTrue("administrators_%d" % user.pk in response.context['form'].fields)
            self.assertTrue("editors_%d" % user.pk in response.context['form'].fields)
            self.assertTrue("viewers_%d" % user.pk in response.context['form'].fields)

        self.assertFalse(response.context['form'].fields['emails'].initial)
        self.assertEquals('V', response.context['form'].fields['user_group'].initial)

        post_data = dict()

        # keep all the admins
        post_data['administrators_%d' % self.admin.pk] = 'on'
        post_data['administrators_%d' % self.user.pk] = 'on'
        post_data['administrators_%d' % self.root.pk] = 'on'

        # add self.root to editors
        post_data['editors_%d' % self.root.pk] = 'on'
        post_data['user_group'] = 'E'

        response = self.client.post(manage_accounts_url, post_data)
        self.assertEquals(302, response.status_code)

        org = Org.objects.get(pk=self.org.pk)
        self.assertEquals(org.administrators.all().count(), 3)
        self.assertFalse(org.viewers.all())
        self.assertTrue(org.editors.all())
        self.assertEquals(org.editors.all()[0].pk, self.root.pk)

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

    def test_join(self):
        editor_invitation = Invitation.objects.create(org=self.org,
                                               user_group="E",
                                               email="norkans7@gmail.com",
                                               created_by=self.admin,
                                               modified_by=self.admin)


        editor_join_url = reverse('orgs.org_join', args=[editor_invitation.secret])
        self.client.logout()

        # if no user is logged we redirect to the create_login page
        response = self.client.get(editor_join_url)
        self.assertEquals(302, response.status_code)
        response = self.client.get(editor_join_url, follow=True)
        self.assertEquals(response.request['PATH_INFO'], reverse('orgs.org_create_login', args=[editor_invitation.secret]))

        # a user is already logged in
        self.invited_editor = self.create_user("InvitedEditor")
        self.login(self.invited_editor)

        response = self.client.get(editor_join_url)
        self.assertEquals(200, response.status_code)

        self.assertEquals(self.org.pk, response.context['org'].pk)
        # we have a form without field except one 'loc'
        self.assertEquals(1, len(response.context['form'].fields))

        post_data = dict()
        response = self.client.post(editor_join_url, post_data, follow=True)
        self.assertEquals(200, response.status_code)

        self.assertTrue(self.invited_editor in self.org.editors.all())
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
        self.login(self.non_org_manager)
        response = self.client.get(choose_url)
        self.assertEquals(200, response.status_code)
        self.assertEquals(0, len(response.context['orgs']))
        self.assertContains(response, "Your account is not associated with any organization. Please contact your administrator to receive an invitation to an organization.")

        # superuser gets redirected to user management page
        self.login(self.superuser)
        response = self.client.get(choose_url, follow=True)
        self.assertContains(response, "Organizations")

    def test_decrement_topups(self):
        # we start with 1000 credits, try decrementing it
        active = self.org._calculate_active_topup()

        topup_id = self.org.decrement_credit()
        self.assertEquals(active.pk, topup_id)

        # we should have a key that is saving our topup and number of credits
        r = get_redis_connection()
        self.assertEquals(topup_id, int(r.get(ORG_ACTIVE_TOPUP_CACHE_KEY % self.org.pk)))
        self.assertEquals(999, int(r.get(ORG_TOPUP_CREDITS_CACHE_KEY % self.org.pk)))

        # to test it is truly using the cache, decrement our topup_credits in redis
        # and see if it goes down
        r.set(ORG_TOPUP_CREDITS_CACHE_KEY % self.org.pk, 501)

        topup_id = self.org.decrement_credit()
        self.assertEquals(active.pk, topup_id)
        self.assertEquals(500, int(r.get(ORG_TOPUP_CREDITS_CACHE_KEY % self.org.pk)))

        # and that we properly recalculate for the 0 credit case
        r.set(ORG_TOPUP_CREDITS_CACHE_KEY % self.org.pk, 0)

        topup_id = self.org.decrement_credit()
        self.assertEquals(active.pk, topup_id)
        self.assertEquals(topup_id, int(r.get(ORG_ACTIVE_TOPUP_CACHE_KEY % self.org.pk)))
        self.assertEquals(999, int(r.get(ORG_TOPUP_CREDITS_CACHE_KEY % self.org.pk)))

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
        test_contact = self.create_contact("Test Contact", "+12065551212")
        test_contact.is_test = True
        test_contact.save()
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
        self.assertEquals(10, TopUp.objects.get(pk=welcome_topup.pk).used)

        # reduce our credits on our topup to 15
        TopUp.objects.filter(pk=welcome_topup.pk).update(credits=15)
        self.org.update_caches(OrgEvent.topup_updated, None)  # invalidates our credits remaining cache

        self.assertEquals(15, self.org.get_credits_total())
        self.assertEquals(5, self.org.get_credits_remaining())

        # create 10 more messages, only 5 of which will get a topup
        create_msgs(contact, 10)

        self.assertEquals(15, TopUp.objects.get(pk=welcome_topup.pk).msgs.count())
        self.assertEquals(15, TopUp.objects.get(pk=welcome_topup.pk).used)

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

        self.assertEquals(15, TopUp.objects.get(pk=welcome_topup.pk).used)

        # raise our topup to take 20 and create another for 5
        TopUp.objects.filter(pk=welcome_topup.pk).update(credits=20)
        new_topup = TopUp.create(self.admin, price=0, credits=5)
        self.org.update_caches(OrgEvent.topup_updated, None)

        # apply topups which will max out both and reduce debt to 5
        self.org.apply_topups()

        self.assertEquals(20, welcome_topup.msgs.count())
        self.assertEquals(20, TopUp.objects.get(pk=welcome_topup.pk).used)
        self.assertEquals(5, new_topup.msgs.count())
        self.assertEquals(5, TopUp.objects.get(pk=new_topup.pk).used)
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
        self.assertEquals(5, TopUp.objects.get(pk=mega_topup.pk).used)

        # now we're pro
        self.assertTrue(self.org.is_pro())
        self.assertEquals(100025, self.org.get_credits_total())
        self.assertEquals(30, self.org.get_credits_used())
        self.assertEquals(99995, self.org.get_credits_remaining())

        # and new messages use the mega topup
        msg = self.create_msg(contact=contact, direction='I', text="Test")
        self.assertEquals(msg.topup, mega_topup)

        self.assertEquals(6, TopUp.objects.get(pk=mega_topup.pk).used)

        # but now it expires
        yesterday = timezone.now() - relativedelta(days=1)
        mega_topup.expires_on = yesterday
        mega_topup.save()

        r = get_redis_connection()
        r.set(ORG_TOPUP_EXPIRES_CACHE_KEY % self.org.pk, datetime_to_ms(yesterday))

        # new incoming messages should not be assigned a topup
        msg = self.create_msg(contact=contact, direction='I', text="Test")
        self.assertIsNone(msg.topup)

        # check our totals
        self.org.update_caches(OrgEvent.topup_updated, None)

        with self.assertNumQueries(3):
            self.assertEquals(31, self.org.get_credits_total())
            self.assertEquals(32, self.org.get_credits_used())
            self.assertEquals(-1, self.org.get_credits_remaining())

    test_topups.active = True

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

        post_data = dict()
        post_data['account_sid'] = "ACe54dc36bfd2a3b483b7ed854b2dd40c1"
        post_data['account_token'] = "0b14d47901387c03f92253a4e4449d5e"

        response = self.client.post(connect_url, post_data)

        org = Org.objects.get(pk=self.org.pk)
        self.assertEquals(org.config_json()['ACCOUNT_SID'], "ACe54dc36bfd2a3b483b7ed854b2dd40c1")
        self.assertEquals(org.config_json()['ACCOUNT_TOKEN'], "0b14d47901387c03f92253a4e4449d5e")
        self.assertTrue(org.config_json()['APPLICATION_SID'])

        twilio_account_url = reverse('orgs.org_twilio_account')
        response = self.client.get(twilio_account_url)
        self.assertEquals("ACe54dc36bfd2a3b483b7ed854b2dd40c1", response.context['config']['ACCOUNT_SID'])

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


    def test_patch_folder_queryset(self):
        self.create_contact(name="Bob", number="123")
        self.create_contact(name="Jim", number="234")
        self.create_contact(name="Ann", number="345")

        contact_qs = self.org.get_folder_queryset(OrgFolder.contacts_all)

        with self.assertNumQueries(1):
            self.assertEquals(3, contact_qs.count())

        self.org.patch_folder_queryset(contact_qs, OrgFolder.contacts_all, None)

        with self.assertNumQueries(1):
            self.assertEquals(3, contact_qs.count())  # count not yet in cache
        with self.assertNumQueries(0):
            self.assertEquals(3, contact_qs.count())  # count taken from cache

        # simulate a wrong number for the cached count
        r = get_redis_connection()
        cache_key = self.org._get_folder_count_cache_key(OrgFolder.contacts_all)

        r.set(cache_key, 10)
        self.assertEquals(10, contact_qs.count())  # wrong but no way of knowing

        r.set(cache_key, -7)
        self.assertEquals(3, contact_qs.count())  # negative so recognized as wrong and ignored

    def test_contact_folder_counts(self):
        folders = (OrgFolder.contacts_all, OrgFolder.contacts_failed, OrgFolder.contacts_blocked)
        get_all_counts = lambda org: {key.name: org.get_folder_count(key) for key in folders}

        with self.assertNumQueries(3):  # from db
            self.assertEqual(dict(contacts_all=0, contacts_failed=0, contacts_blocked=0), get_all_counts(self.org))
        with self.assertNumQueries(0):  # from cache
            self.assertEqual(dict(contacts_all=0, contacts_failed=0, contacts_blocked=0), get_all_counts(self.org))

        with self.assertNumQueries(0):
            self.assertFalse(self.org.has_contacts())

        hannibal = self.create_contact("Hannibal", number="0783835001")
        face = self.create_contact("Face", number="0783835002")
        ba = self.create_contact("B.A.", number="0783835003")
        murdock = self.create_contact("Murdock", number="0783835004")

        with self.assertNumQueries(0):
            self.assertTrue(self.org.has_contacts())
            self.assertEqual(dict(contacts_all=4, contacts_failed=0, contacts_blocked=0), get_all_counts(self.org))

        # call methods twice to check counts don't change twice
        murdock.block()
        murdock.block()
        face.block()
        ba.fail()
        ba.fail()

        with self.assertNumQueries(0):
            self.assertEqual(dict(contacts_all=2, contacts_failed=1, contacts_blocked=2), get_all_counts(self.org))

        murdock.release()
        murdock.release()
        face.unblock()
        face.unblock()
        ba.unfail()
        ba.unfail()

        with self.assertNumQueries(0):
            self.assertEqual(dict(contacts_all=3, contacts_failed=0, contacts_blocked=0), get_all_counts(self.org))

        self.org.clear_caches([OrgCache.display])

        with self.assertNumQueries(3):
            self.assertEqual(dict(contacts_all=3, contacts_failed=0, contacts_blocked=0), get_all_counts(self.org))

    def test_message_folder_counts(self):
        r = get_redis_connection()

        folders = (OrgFolder.msgs_inbox, OrgFolder.msgs_archived, OrgFolder.msgs_outbox, OrgFolder.broadcasts_outbox,
                   OrgFolder.calls_all, OrgFolder.msgs_flows, OrgFolder.broadcasts_scheduled, OrgFolder.msgs_failed)
        get_all_counts = lambda org: {key.name: org.get_folder_count(key) for key in folders}

        with self.assertNumQueries(8):  # from db
            self.assertEqual(dict(msgs_inbox=0, msgs_archived=0, msgs_outbox=0, broadcasts_outbox=0, calls_all=0,
                                  msgs_flows=0, broadcasts_scheduled=0, msgs_failed=0), get_all_counts(self.org))
        with self.assertNumQueries(0):  # from cache
            self.assertEqual(dict(msgs_inbox=0, msgs_archived=0, msgs_outbox=0, broadcasts_outbox=0, calls_all=0,
                                  msgs_flows=0, broadcasts_scheduled=0, msgs_failed=0), get_all_counts(self.org))

        with self.assertNumQueries(0):
            self.assertFalse(self.org.has_messages())

        contact1 = self.create_contact("Bob", number="0783835001")
        contact2 = self.create_contact("Jim", number="0783835002")
        msg1 = Msg.create_incoming(self.channel, (TEL_SCHEME, "0783835001"), text="Message 1")
        msg2 = Msg.create_incoming(self.channel, (TEL_SCHEME, "0783835001"), text="Message 2")
        msg3 = Msg.create_incoming(self.channel, (TEL_SCHEME, "0783835001"), text="Message 3")
        msg4 = Msg.create_incoming(self.channel, (TEL_SCHEME, "0783835001"), text="Message 4")
        Call.create_call(self.channel, "0783835001", timezone.now(), 10, CALL_IN)
        bcast1 = Broadcast.create(self.org, self.user, "Broadcast 1", [contact1, contact2])
        bcast2 = Broadcast.create(self.org, self.user, "Broadcast 2", [contact1, contact2],
                                  schedule=Schedule.create_schedule(timezone.now(), 'D', self.user))

        with self.assertNumQueries(0):
            self.assertTrue(self.org.has_messages())
            self.assertEqual(dict(msgs_inbox=4, msgs_archived=0, msgs_outbox=0, broadcasts_outbox=1, calls_all=1,
                                  msgs_flows=0, broadcasts_scheduled=1, msgs_failed=0), get_all_counts(self.org))

        msg3.archive()
        bcast1.send()
        msg5, msg6 = tuple(Msg.objects.filter(broadcast=bcast1))
        Call.create_call(self.channel, "0783835002", timezone.now(), 10, CALL_IN)
        Broadcast.create(self.org, self.user, "Broadcast 3", [contact1],
                         schedule=Schedule.create_schedule(timezone.now(), 'W', self.user))

        with self.assertNumQueries(0):
            self.assertEqual(dict(msgs_inbox=3, msgs_archived=1, msgs_outbox=2, broadcasts_outbox=1, calls_all=2,
                                  msgs_flows=0, broadcasts_scheduled=2, msgs_failed=0), get_all_counts(self.org))

        msg1.archive()
        msg3.release()  # deleting an archived msg
        msg4.release()  # deleting a visible msg
        msg5.fail()

        with self.assertNumQueries(0):
            self.assertEqual(dict(msgs_inbox=1, msgs_archived=1, msgs_outbox=2, broadcasts_outbox=1, calls_all=2,
                                  msgs_flows=0, broadcasts_scheduled=2, msgs_failed=1), get_all_counts(self.org))

        msg1.restore()
        msg3.release()  # already released
        msg5.fail()  # already failed

        with self.assertNumQueries(0):
            self.assertEqual(dict(msgs_inbox=2, msgs_archived=0, msgs_outbox=2, broadcasts_outbox=1, calls_all=2,
                                  msgs_flows=0, broadcasts_scheduled=2, msgs_failed=1), get_all_counts(self.org))

        Msg.mark_error(r, msg6)
        Msg.mark_error(r, msg6)
        Msg.mark_error(r, msg6)
        Msg.mark_error(r, msg6)

        with self.assertNumQueries(0):
            self.assertEqual(dict(msgs_inbox=2, msgs_archived=0, msgs_outbox=2, broadcasts_outbox=1, calls_all=2,
                                  msgs_flows=0, broadcasts_scheduled=2, msgs_failed=2), get_all_counts(self.org))

        self.org.clear_caches([OrgCache.display])

        with self.assertNumQueries(8):
            self.assertEqual(dict(msgs_inbox=2, msgs_archived=0, msgs_outbox=2, broadcasts_outbox=1, calls_all=2,
                                  msgs_flows=0, broadcasts_scheduled=2, msgs_failed=2), get_all_counts(self.org))

    def test_download(self):
        messages_export_task = ExportMessagesTask.objects.create(org=self.org, host='rapidpro.io',
                                                                 created_by=self.admin, modified_by=self.admin)

        self.assertLoginRedirect(self.client.get('/org/download/messages/%s/' % messages_export_task.pk))

        self.login(self.admin)

        response = self.client.get('/org/download/messages/%s/' % messages_export_task.pk)
        self.assertEquals(302, response.status_code)
        response = self.client.get('/org/download/messages/%s/' % messages_export_task.pk, follow=True)
        self.assertEquals(reverse('msgs.msg_inbox'), response.request['PATH_INFO'])

        messages_export_task.do_export()

        response = self.client.get('/org/download/messages/%s/' % messages_export_task.pk)
        self.assertEquals(200, response.status_code)

        response = self.client.get('/org/download/contacts/%s/' % messages_export_task.pk)
        self.assertEquals(302, response.status_code)
        response = self.client.get('/org/download/contacts/%s/' % messages_export_task.pk, follow=True)
        self.assertEquals(reverse('msgs.msg_inbox'), response.request['PATH_INFO'])

        contact_export_task = ExportContactsTask.objects.create(org=self.org, host='rapidpro.io',
                                                                created_by=self.admin, modified_by=self.admin)
        contact_export_task.do_export()

        flow = self.create_flow()
        flow_export_task = ExportFlowResultsTask.objects.create(org=self.org, host='rapidpro.io',
                                                                created_by=self.admin, modified_by=self.admin)

        flow_export_task.flows.add(flow)
        flow_export_task.do_export()

        response = self.client.get('/org/download/contacts/%s/' % contact_export_task.pk, follow=True)
        self.assertEquals(200, response.status_code)

        response = self.client.get('/org/download/flows/%s/' % flow_export_task.pk, follow=True)
        self.assertEquals(200, response.status_code)

        self.create_secondary_org()
        self.org2.administrators.add(self.admin)

        self.admin.set_org(self.org2)
        s = self.client.session
        s['org_id'] = self.org2.pk
        s.save()

        response = self.client.get('/org/download/messages/%s/' % messages_export_task.pk)
        self.assertEquals(200, response.status_code)
        user = response.context_data['view'].request.user
        self.assertEquals(user, self.admin)
        self.assertEquals(user.get_org(), self.org2)

        self.admin.set_org(None)
        s = self.client.session
        s['org_id'] = None
        s.save()

        response = self.client.get('/org/download/messages/%s/' % messages_export_task.pk)
        self.assertEquals(200, response.status_code)
        user = response.context_data['view'].request.user
        self.assertEquals(user, self.admin)
        self.assertEquals(user.get_org(), messages_export_task.org)
        self.assertEquals(user.get_org(), self.org)


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

        # shouldn't show the number on the outgoing page (for now this only shows recipient count)
        response = self.client.get(reverse('msgs.broadcast_outbox'))

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
        self.assertTrue(user.api_token())

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
        response = self.client.post(reverse('orgs.org_webhook'), dict(webhook='http://www.foo.com/', mt_sms=1))
        self.assertRedirect(response, reverse('orgs.org_home'))

        org = Org.objects.get(name="Relieves World")
        self.assertEquals("http://www.foo.com/", org.webhook)
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
        Channel.objects.create(name="Twitter", channel_type=TWITTER, role="SR", org=self.org,
                               created_by=self.user, modified_by=self.user)
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


class BulkExportTest(TembaTest):

    def test_trigger_flow(self):

        self.import_file('triggered-flow')

        flow = Flow.objects.filter(name='Trigger a Flow', org=self.org).first()
        definition = flow.as_json()
        actions = definition[Flow.ACTION_SETS][0]['actions']
        self.assertEquals(1, len(actions))
        self.assertEquals('Triggered Flow', actions[0]['name'])

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
            self.assertEquals(3, ContactGroup.objects.filter(org=self.org).count())
            self.assertEquals(1, Label.objects.filter(org=self.org).count())

        # import all our bits
        self.import_file('the-clinic')

        # check that the right number of objects successfully imported for our app
        assert_object_counts()

        # let's update some stuff
        confirm_appointment = Flow.objects.get(name='Confirm Appointment')
        action_set = confirm_appointment.action_sets.order_by('-y').first()
        actions = action_set.get_actions_dict()
        actions[0]['msg'] = 'Thanks for nothing'
        action_set.set_actions_dict(actions)
        action_set.save()

        trigger = Trigger.objects.filter(keyword='patient').first()
        trigger.flow = confirm_appointment
        trigger.save()

        message_flow = Flow.objects.filter(flow_type='M').order_by('pk').first()
        action_set = message_flow.action_sets.order_by('-y').first()
        actions = action_set.get_actions_dict()
        self.assertEquals("Hi there, just a quick reminder that you have an appointment at The Clinic at @contact.next_appointment. If you can't make it please call 1-888-THE-CLINIC.", actions[0]['msg'])
        actions[0]['msg'] = 'No reminders for you!'
        action_set.set_actions_dict(actions)
        action_set.save()

        # now reimport
        self.import_file('the-clinic')

        # our flow should get reset from the import
        action_set = Flow.objects.get(pk=confirm_appointment.pk).action_sets.order_by('-y').first()
        actions = action_set.get_actions_dict()
        self.assertEquals("Thanks, your appointment at The Clinic has been confirmed for @contact.next_appointment. See you then!", actions[0]['msg'])

        # same with our trigger
        trigger = Trigger.objects.filter(keyword='patient').first()
        self.assertEquals(Flow.objects.filter(name='Register Patient').first(), trigger.flow)

        # our old campaign message flow should be gone now
        self.assertIsNone(Flow.objects.filter(pk=message_flow.pk).first())

        # find our new message flow, and see that the original message is there
        message_flow = Flow.objects.filter(flow_type='M').order_by('pk').first()
        action_set = Flow.objects.get(pk=message_flow.pk).action_sets.order_by('-y').first()
        actions = action_set.get_actions_dict()
        self.assertEquals("Hi there, just a quick reminder that you have an appointment at The Clinic at @contact.next_appointment. If you can't make it please call 1-888-THE-CLINIC.", actions[0]['msg'])

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
        response = json.loads(response.content)
        self.assertEquals(4, response.get('version', 0))
        self.assertEquals('http://rapidpro.io', response.get('site', None))

        self.assertEquals(8, len(response.get('flows', [])))
        self.assertEquals(4, len(response.get('triggers', [])))
        self.assertEquals(1, len(response.get('campaigns', [])))

        # finally let's try importing our exported file
        self.org.import_app(response, self.admin, site='http://rapidpro.io')
        assert_object_counts()

        # let's rename a flow and import our export again
        flow = Flow.objects.get(name='Confirm Appointment')
        flow.name = "A new flow"
        flow.save()

        campaign = Campaign.objects.all().first()
        campaign.name = "A new campagin"
        campaign.save()

        group = ContactGroup.objects.filter(name='Pending Appointments').first()
        group.name = "A new group"
        group.save()

        # it should fall back on ids and not create new objects even though the names changed
        self.org.import_app(response, self.admin, site='http://rapidpro.io')
        assert_object_counts()

        # and our objets should have the same names as before
        self.assertEquals('Confirm Appointment', Flow.objects.get(pk=flow.pk).name)
        self.assertEquals('Appointment Schedule', Campaign.objects.all().first().name)
        self.assertEquals('Pending Appointments', ContactGroup.objects.get(pk=group.pk).name)

        # let's rename our objects again
        flow.name = "A new name"
        flow.save()

        campaign.name = "A new campagin"
        campaign.save()

        group.name = "A new group"
        group.save()

        # now import the same import but pretend its from a different site
        self.org.import_app(response, self.admin, site='http://temba.io')

        # the newly named objects won't get updated in this case and we'll create new ones instead
        self.assertEquals(9, Flow.objects.filter(org=self.org, is_archived=False, flow_type='F').count())
        self.assertEquals(2, Campaign.objects.filter(org=self.org, is_archived=False).count())
        self.assertEquals(4, ContactGroup.objects.filter(org=self.org).count())

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


