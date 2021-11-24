import io
import smtplib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlencode

import pytz
import stripe
import stripe.error
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta
from smartmin.users.models import FailedLogin, RecoveryToken

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba import mailroom
from temba.airtime.models import AirtimeTransfer
from temba.api.models import APIToken, Resthook, WebHookEvent
from temba.archives.models import Archive
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import Alert, Channel, SyncEvent
from temba.classifiers.models import Classifier
from temba.classifiers.types.wit import WitType
from temba.contacts.models import (
    URN,
    Contact,
    ContactField,
    ContactGroup,
    ContactImport,
    ContactImportBatch,
    ContactURN,
    ExportContactsTask,
)
from temba.flows.models import ExportFlowResultsTask, Flow, FlowLabel, FlowRun, FlowStart
from temba.globals.models import Global
from temba.locations.models import AdminBoundary
from temba.msgs.models import Broadcast, ExportMessagesTask, Label, Msg
from temba.notifications.models import Notification
from temba.orgs.models import BackupToken, Debit, OrgActivity
from temba.orgs.tasks import suspend_topup_orgs_task
from temba.request_logs.models import HTTPLog
from temba.templates.models import Template, TemplateTranslation
from temba.tests import (
    CRUDLTestMixin,
    ESMockWithScroll,
    MockResponse,
    TembaNonAtomicTest,
    TembaTest,
    matchers,
    mock_mailroom,
)
from temba.tests.engine import MockSessionWriter
from temba.tests.s3 import MockS3Client, jsonlgz_encode
from temba.tests.twilio import MockRequestValidator, MockTwilioClient
from temba.tickets.models import Ticketer
from temba.tickets.types.mailgun import MailgunType
from temba.triggers.models import Trigger
from temba.utils import dict_to_struct, json, languages

from .context_processors import GroupPermWrapper
from .models import CreditAlert, Invitation, Org, OrgRole, TopUp, TopUpCredits
from .tasks import delete_orgs_task, resume_failed_tasks, squash_topupcredits


class OrgRoleTest(TembaTest):
    def test_from_code(self):
        self.assertEqual(OrgRole.EDITOR, OrgRole.from_code("E"))
        self.assertIsNone(OrgRole.from_code("X"))

    def test_from_group(self):
        self.assertEqual(OrgRole.EDITOR, OrgRole.from_group(Group.objects.get(name="Editors")))
        self.assertIsNone(OrgRole.from_group(Group.objects.get(name="Beta")))

    def test_group(self):
        self.assertEqual(Group.objects.get(name="Editors"), OrgRole.EDITOR.group)
        self.assertEqual(Group.objects.get(name="Agents"), OrgRole.AGENT.group)


class OrgContextProcessorTest(TembaTest):
    def test_group_perms_wrapper(self):
        administrators = Group.objects.get(name="Administrators")
        editors = Group.objects.get(name="Editors")
        viewers = Group.objects.get(name="Viewers")

        perms = GroupPermWrapper(administrators)

        self.assertTrue(perms["msgs"]["msg_inbox"])
        self.assertTrue(perms["contacts"]["contact_update"])
        self.assertTrue(perms["orgs"]["org_country"])
        self.assertTrue(perms["orgs"]["org_manage_accounts"])
        self.assertFalse(perms["orgs"]["org_delete"])

        perms = GroupPermWrapper(editors)

        self.assertTrue(perms["msgs"]["msg_inbox"])
        self.assertTrue(perms["contacts"]["contact_update"])
        self.assertFalse(perms["orgs"]["org_manage_accounts"])
        self.assertFalse(perms["orgs"]["org_delete"])

        perms = GroupPermWrapper(viewers)

        self.assertTrue(perms["msgs"]["msg_inbox"])
        self.assertFalse(perms["contacts"]["contact_update"])
        self.assertFalse(perms["orgs"]["org_manage_accounts"])
        self.assertFalse(perms["orgs"]["org_delete"])

        self.assertFalse(perms["msgs"]["foo"])  # no blow up if perm doesn't exist
        self.assertFalse(perms["chickens"]["foo"])  # or app doesn't exist

        with self.assertRaises(TypeError):
            list(perms)


class UserTest(TembaTest):
    def test_model(self):
        user = User.objects.create(
            username="jim@rapidpro.io", email="jim@rapidpro.io", password="super", first_name="Jim", last_name="McFlow"
        )

        self.assertFalse(user.is_beta())
        self.assertFalse(user.is_support())
        self.assertEqual("Jim McFlow", user.name)
        self.assertEqual({"email": "jim@rapidpro.io", "name": "Jim McFlow"}, user.as_engine_ref())

        user.last_name = ""
        user.save(update_fields=("last_name",))

        self.assertEqual("Jim", user.name)
        self.assertEqual({"email": "jim@rapidpro.io", "name": "Jim"}, user.as_engine_ref())

    def test_login(self):
        login_url = reverse("users.user_login")
        verify_url = reverse("users.two_factor_verify")
        backup_url = reverse("users.two_factor_backup")

        user_settings = self.admin.get_settings()
        self.assertIsNone(user_settings.last_auth_on)

        # try to access a non-public page
        response = self.client.get(reverse("msgs.msg_inbox"))
        self.assertLoginRedirect(response)
        self.assertTrue(response.url.endswith("?next=/msg/inbox/"))

        # view login page
        response = self.client.get(login_url)
        self.assertEqual(200, response.status_code)

        # submit incorrect username and password
        response = self.client.post(login_url, {"username": "jim", "password": "pass123"})
        self.assertEqual(200, response.status_code)
        self.assertFormError(
            response,
            "form",
            "__all__",
            "Please enter a correct username and password. Note that both fields may be case-sensitive.",
        )

        # submit correct username and password
        response = self.client.post(login_url, {"username": "Administrator", "password": "Administrator"})
        self.assertRedirect(response, reverse("orgs.org_choose"))

        user_settings = self.admin.get_settings()
        self.assertIsNotNone(user_settings.last_auth_on)

        # logout and enable 2FA
        self.client.logout()
        self.admin.enable_2fa()

        # can't access two-factor verify page yet
        response = self.client.get(verify_url)
        self.assertLoginRedirect(response)

        # login via login page again
        response = self.client.post(
            login_url + "?next=/msg/inbox/", {"username": "Administrator", "password": "Administrator"}
        )
        self.assertRedirect(response, verify_url)
        self.assertTrue(response.url.endswith("?next=/msg/inbox/"))

        # view two-factor verify page
        response = self.client.get(verify_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(["otp"], list(response.context["form"].fields.keys()))
        self.assertContains(response, backup_url)

        # enter invalid OTP
        response = self.client.post(verify_url, {"otp": "nope"})
        self.assertFormError(response, "form", "otp", "Incorrect OTP. Please try again.")

        # enter valid OTP
        with patch("pyotp.TOTP.verify", return_value=True):
            response = self.client.post(verify_url, {"otp": "123456"})
        self.assertRedirect(response, reverse("orgs.org_choose"))

        self.client.logout()

        # login via login page again
        response = self.client.post(login_url, {"username": "Administrator", "password": "Administrator"})
        self.assertRedirect(response, verify_url)

        # but this time we've lost our phone so go to the page for backup tokens
        response = self.client.get(backup_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(["token"], list(response.context["form"].fields.keys()))

        # enter invalid backup token
        response = self.client.post(backup_url, {"token": "nope"})
        self.assertFormError(response, "form", "token", "Invalid backup token. Please try again.")

        # enter valid backup token
        response = self.client.post(backup_url, {"token": self.admin.backup_tokens.first()})
        self.assertRedirect(response, reverse("orgs.org_choose"))

        self.assertEqual(9, len(self.admin.backup_tokens.filter(is_used=False)))

    @override_settings(USER_LOCKOUT_TIMEOUT=1, USER_FAILED_LOGIN_LIMIT=3)
    def test_login_lockouts(self):
        login_url = reverse("users.user_login")
        verify_url = reverse("users.two_factor_verify")
        backup_url = reverse("users.two_factor_backup")
        failed_url = reverse("users.user_failed")

        # submit incorrect username and password 3 times
        self.client.post(login_url, {"username": "Administrator", "password": "pass123"})
        self.client.post(login_url, {"username": "Administrator", "password": "pass123"})
        response = self.client.post(login_url, {"username": "Administrator", "password": "pass123"})

        self.assertRedirect(response, failed_url)
        self.assertRedirect(self.client.get(reverse("msgs.msg_inbox")), login_url)

        # simulate failed logins timing out by making them older
        FailedLogin.objects.all().update(failed_on=timezone.now() - timedelta(minutes=3))

        # now we're allowed to make failed logins again
        response = self.client.post(login_url, {"username": "Administrator", "password": "pass123"})
        self.assertFormError(
            response,
            "form",
            "__all__",
            "Please enter a correct username and password. Note that both fields may be case-sensitive.",
        )

        # and successful logins
        response = self.client.post(login_url, {"username": "Administrator", "password": "Administrator"})
        self.assertRedirect(response, reverse("orgs.org_choose"))

        # try again with 2FA enabled
        self.client.logout()
        self.admin.enable_2fa()

        # submit incorrect username and password 3 times
        self.client.post(login_url, {"username": "Administrator", "password": "pass123"})
        self.client.post(login_url, {"username": "Administrator", "password": "pass123"})
        response = self.client.post(login_url, {"username": "Administrator", "password": "pass123"})

        self.assertRedirect(response, failed_url)
        self.assertRedirect(self.client.get(reverse("msgs.msg_inbox")), login_url)

        # login correctly
        FailedLogin.objects.all().delete()
        response = self.client.post(login_url, {"username": "Administrator", "password": "Administrator"})
        self.assertRedirect(response, verify_url)

        # now enter a backup token 3 times incorrectly
        self.client.post(backup_url, {"token": "nope"})
        self.client.post(backup_url, {"token": "nope"})
        response = self.client.post(backup_url, {"token": "nope"})

        self.assertRedirect(response, failed_url)
        self.assertRedirect(self.client.get(verify_url), login_url)
        self.assertRedirect(self.client.get(backup_url), login_url)
        self.assertRedirect(self.client.get(reverse("msgs.msg_inbox")), login_url)

        # simulate failed logins timing out by making them older
        FailedLogin.objects.all().update(failed_on=timezone.now() - timedelta(minutes=3))

        # we can't enter backup tokens again without going thru regular login first
        response = self.client.post(backup_url, {"token": "nope"})
        self.assertRedirect(response, login_url)

        response = self.client.post(login_url, {"username": "Administrator", "password": "Administrator"})
        self.assertRedirect(response, verify_url)

        response = self.client.post(backup_url, {"token": self.admin.backup_tokens.first()})
        self.assertRedirect(response, reverse("orgs.org_choose"))

    def test_two_factor(self):
        self.assertFalse(self.admin.get_settings().two_factor_enabled)

        self.admin.enable_2fa()

        self.assertTrue(self.admin.get_settings().two_factor_enabled)
        self.assertEqual(10, len(self.admin.backup_tokens.filter(is_used=False)))

        # try to verify with.. nothing
        self.assertFalse(self.admin.verify_2fa())

        # try to verify with an invalid OTP
        self.assertFalse(self.admin.verify_2fa(otp="nope"))

        # try to verify with a valid OTP
        with patch("pyotp.TOTP.verify", return_value=True):
            self.assertTrue(self.admin.verify_2fa(otp="123456"))

        # try to verify with an invalid backup token
        self.assertFalse(self.admin.verify_2fa(backup_token="nope"))

        # try to verify with a valid backup token
        token = self.admin.backup_tokens.first().token
        self.assertTrue(self.admin.verify_2fa(backup_token=token))

        self.assertEqual(9, len(self.admin.backup_tokens.filter(is_used=False)))

        # can't verify again with same backup token
        self.assertFalse(self.admin.verify_2fa(backup_token=token))

        self.admin.disable_2fa()

        self.assertFalse(self.admin.get_settings().two_factor_enabled)

    def test_two_factor_views(self):
        enable_url = reverse("orgs.user_two_factor_enable")
        tokens_url = reverse("orgs.user_two_factor_tokens")
        disable_url = reverse("orgs.user_two_factor_disable")

        self.login(self.admin, update_last_auth_on=False)

        # org home page tells us 2FA is disabled, links to page to enable it
        response = self.client.get(reverse("orgs.org_home"))
        self.assertContains(response, "Two-factor authentication is <b>disabled</b>")
        self.assertContains(response, enable_url)

        # view form to enable 2FA
        response = self.client.get(enable_url)
        self.assertEqual(["otp", "password", "loc"], list(response.context["form"].fields.keys()))

        # try to submit with no OTP or password
        response = self.client.post(enable_url, {})
        self.assertFormError(response, "form", "otp", "This field is required.")
        self.assertFormError(response, "form", "password", "This field is required.")

        # try to submit with invalid OTP and password
        response = self.client.post(enable_url, {"otp": "nope", "password": "wrong"})
        self.assertFormError(response, "form", "otp", "OTP incorrect. Please try again.")
        self.assertFormError(response, "form", "password", "Password incorrect.")

        # submit with valid OTP and password
        with patch("pyotp.TOTP.verify", return_value=True):
            response = self.client.post(enable_url, {"otp": "123456", "password": "Administrator"})
        self.assertRedirect(response, tokens_url)
        self.assertTrue(self.admin.get_settings().two_factor_enabled)

        # org home page now tells us 2FA is enabled, links to page manage tokens
        response = self.client.get(reverse("orgs.org_home"))
        self.assertContains(response, "Two-factor authentication is <b>enabled</b>")

        # view backup tokens page
        response = self.client.get(tokens_url)
        self.assertContains(response, "Regenerate Tokens")
        self.assertContains(response, disable_url)

        tokens = [t.token for t in response.context["backup_tokens"]]

        # posting to that page regenerates tokens
        response = self.client.post(tokens_url)
        self.assertContains(response, "Two-factor authentication backup tokens changed.")
        self.assertNotEqual(tokens, [t.token for t in response.context["backup_tokens"]])

        # view form to disable 2FA
        response = self.client.get(disable_url)
        self.assertEqual(["password", "loc"], list(response.context["form"].fields.keys()))

        # try to submit with no password
        response = self.client.post(disable_url, {})
        self.assertFormError(response, "form", "password", "This field is required.")

        # try to submit with invalid password
        response = self.client.post(disable_url, {"password": "wrong"})
        self.assertFormError(response, "form", "password", "Password incorrect.")

        # submit with valid password
        response = self.client.post(disable_url, {"password": "Administrator"})
        self.assertRedirect(response, reverse("orgs.org_home"))
        self.assertFalse(self.admin.get_settings().two_factor_enabled)

        # trying to view the tokens page now takes us to the enable form
        response = self.client.get(tokens_url)
        self.assertRedirect(response, enable_url)

    def test_two_factor_time_limit(self):
        login_url = reverse("users.user_login")
        verify_url = reverse("users.two_factor_verify")
        backup_url = reverse("users.two_factor_backup")

        self.admin.enable_2fa()

        # simulate a login for a 2FA user 10 minutes ago
        with patch("django.utils.timezone.now", return_value=timezone.now() - timedelta(minutes=10)):
            response = self.client.post(login_url, {"username": "Administrator", "password": "Administrator"})
            self.assertRedirect(response, verify_url)

            response = self.client.get(verify_url)
            self.assertEqual(200, response.status_code)

        # if they access the verify or backup page now, they are redirected back to the login page
        response = self.client.get(verify_url)
        self.assertRedirect(response, login_url)

        response = self.client.get(backup_url)
        self.assertRedirect(response, login_url)

    def test_two_factor_confirm_access(self):
        tokens_url = reverse("orgs.user_two_factor_tokens")

        self.admin.enable_2fa()
        self.login(self.admin, update_last_auth_on=False)

        # org home page tells us 2FA is enabled, links to page manage tokens
        response = self.client.get(reverse("orgs.org_home"))
        self.assertContains(response, "Two-factor authentication is <b>enabled</b>")
        self.assertContains(response, tokens_url)

        # but navigating to tokens page redirects to confirm auth
        response = self.client.get(tokens_url)
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.url.endswith("/users/confirm-access/?next=/user/two_factor_tokens/"))

        confirm_url = response.url

        # view confirm access page
        response = self.client.get(confirm_url)
        self.assertEqual(["password"], list(response.context["form"].fields.keys()))

        # try to submit with incorrect password
        response = self.client.post(confirm_url, {"password": "nope"})
        self.assertFormError(response, "form", "password", "Password incorrect.")

        # submit with real password
        response = self.client.post(confirm_url, {"password": "Administrator"})
        self.assertRedirect(response, tokens_url)

        response = self.client.get(tokens_url)
        self.assertEqual(200, response.status_code)

    @override_settings(USER_LOCKOUT_TIMEOUT=1, USER_FAILED_LOGIN_LIMIT=3)
    def test_confirm_access(self):
        confirm_url = reverse("users.confirm_access") + f"?next=/msg/inbox/"
        failed_url = reverse("users.user_failed")

        # try to access before logging in
        response = self.client.get(confirm_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(confirm_url)
        self.assertEqual(["password"], list(response.context["form"].fields.keys()))

        # try to submit with incorrect password
        response = self.client.post(confirm_url, {"password": "nope"})
        self.assertFormError(response, "form", "password", "Password incorrect.")

        # 2 more times..
        self.client.post(confirm_url, {"password": "nope"})
        response = self.client.post(confirm_url, {"password": "nope"})
        self.assertRedirect(response, failed_url)

        # even correct password now redirects to failed page
        response = self.client.post(confirm_url, {"password": "Administrator"})
        self.assertRedirect(response, failed_url)

        FailedLogin.objects.all().delete()

        # can once again submit incorrect passwords
        response = self.client.post(confirm_url, {"password": "nope"})
        self.assertFormError(response, "form", "password", "Password incorrect.")

        # and also correct ones
        response = self.client.post(confirm_url, {"password": "Administrator"})
        self.assertRedirect(response, "/msg/inbox/")

    def test_ui_permissions(self):
        # non-logged in users can't go here
        response = self.client.get(reverse("orgs.user_list"))
        self.assertRedirect(response, "/users/login/")
        response = self.client.post(reverse("orgs.user_delete", args=(self.editor.pk,)), dict(delete=True))
        self.assertRedirect(response, "/users/login/")

        # either can admins
        self.login(self.admin)
        response = self.client.get(reverse("orgs.user_list"))
        self.assertRedirect(response, "/users/login/")
        response = self.client.post(reverse("orgs.user_delete", args=(self.editor.pk,)), dict(delete=True))
        self.assertRedirect(response, "/users/login/")

        self.editor.refresh_from_db()
        self.assertTrue(self.editor.is_active)

    def test_ui_management(self):

        # only customer support gets in on this sweet action
        self.login(self.customer_support)

        # one of our users should belong to a bunch of orgs
        for i in range(5):
            org = Org.objects.create(
                name=f"Org {i}",
                timezone=pytz.timezone("Africa/Kigali"),
                brand=settings.DEFAULT_BRAND,
                created_by=self.user,
                modified_by=self.user,
            )
            org.administrators.add(self.admin)

        response = self.client.get(reverse("orgs.user_list"))
        self.assertEqual(200, response.status_code)

        # our user with lots of orgs should get ellipsized
        self.assertContains(response, ", ...")

        response = self.client.post(reverse("orgs.user_delete", args=(self.editor.pk,)), dict(delete=True))
        self.assertEqual(302, response.status_code)

        self.editor.refresh_from_db()
        self.assertFalse(self.editor.is_active)

    def test_release_cross_brand(self):
        # create a second org
        branded_org = Org.objects.create(
            name="Other Brand Org",
            timezone=pytz.timezone("Africa/Kigali"),
            brand="some-other-brand.com",
            created_by=self.admin,
            modified_by=self.admin,
        )

        branded_org.administrators.add(self.admin)

        # now release our user on our primary brand
        self.admin.release(self.superuser, brand=settings.DEFAULT_BRAND)

        # our admin should still be good
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertEqual("Administrator@nyaruka.com", self.admin.email)

        # but she should be removed from org
        self.assertFalse(self.admin.get_user_orgs(settings.DEFAULT_BRAND).exists())

        # now lets release her from the branded org
        self.admin.release(self.superuser, brand="some-other-brand.com")

        # now she gets deactivated and ambiguated and belongs to no orgs
        self.assertFalse(self.admin.is_active)
        self.assertNotEqual("Administrator@nyaruka.com", self.admin.email)
        self.assertFalse(self.admin.get_user_orgs().exists())

    def test_brand_aliases(self):
        # set our brand to our custom org
        self.org.brand = "custom-brand.io"
        self.org.save(update_fields=["brand"])

        # create a second org on the .org version
        branded_org = Org.objects.create(
            name="Other Brand Org",
            timezone=pytz.timezone("Africa/Kigali"),
            brand="custom-brand.org",
            created_by=self.admin,
            modified_by=self.admin,
        )
        branded_org.administrators.add(self.admin)
        self.org2.administrators.add(self.admin)

        # log in as admin
        self.login(self.admin)

        # check our choose page
        response = self.client.get(reverse("orgs.org_choose"), SERVER_NAME="custom-brand.org")

        # should contain both orgs
        self.assertContains(response, "Other Brand Org")
        self.assertContains(response, "Temba")
        self.assertNotContains(response, "Trileet Inc")

        # choose it
        response = self.client.post(
            reverse("orgs.org_choose"), dict(organization=self.org.id), SERVER_NAME="custom-brand.org"
        )
        self.assertRedirect(response, "/msg/inbox/")

    def test_release(self):

        # admin doesn't "own" any orgs
        self.assertEqual(0, len(self.admin.get_owned_orgs()))

        # release all but our admin
        self.surveyor.release(self.superuser, brand=self.org.brand)
        self.editor.release(self.superuser, brand=self.org.brand)
        self.user.release(self.superuser, brand=self.org.brand)
        self.agent.release(self.superuser, brand=self.org.brand)

        # still a user left, our org remains active
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_active)

        # now that we are the last user, we own it now
        self.assertEqual(1, len(self.admin.get_owned_orgs()))
        self.admin.release(self.superuser, brand=self.org.brand)

        # and we take our org with us
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)


class OrgDeleteTest(TembaNonAtomicTest):
    def setUp(self):
        self.setUpOrgs()
        self.setUpLocations()

        # set up a sync event and alert on our channel
        SyncEvent.create(
            self.channel,
            dict(pending=[], retry=[], power_source="P", power_status="full", power_level="100", network_type="W"),
            [],
        )
        Alert.objects.create(
            channel=self.channel, alert_type=Alert.TYPE_SMS, created_by=self.admin, modified_by=self.admin
        )

        # create a second child org
        self.child_org = Org.objects.create(
            name="Child Org",
            timezone=pytz.timezone("Africa/Kigali"),
            country=self.country,
            brand=settings.DEFAULT_BRAND,
            created_by=self.user,
            modified_by=self.user,
        )

        # and give it its own channel
        self.child_channel = self.create_channel(
            "A",
            "Test Channel",
            "+250785551212",
            secret="54321",
            config={Channel.CONFIG_FCM_ID: "123"},
            country="RW",
            org=self.child_org,
        )

        # add a classifier
        self.c1 = Classifier.create(self.org, self.admin, WitType.slug, "Booker", {}, sync=False)

        # add a global
        self.global1 = Global.get_or_create(self.org, self.admin, "org_name", "Org Name", "Acme Ltd")

        HTTPLog.objects.create(
            classifier=self.c1,
            url="http://org2.bar/zap",
            request="GET /zap",
            response=" OK 200",
            is_error=False,
            log_type=HTTPLog.CLASSIFIER_CALLED,
            request_time=10,
            org=self.org,
        )

        # our user is a member of two orgs
        self.parent_org = self.org
        self.child_org.administrators.add(self.user)
        self.child_org.initialize(topup_size=0)
        self.child_org.parent = self.parent_org
        self.child_org.save()

        # now allocate some credits to our child org
        self.org.allocate_credits(self.admin, self.child_org, 300)

        parent_contact = self.create_contact("Parent Contact", phone="+2345123", org=self.parent_org)
        child_contact = self.create_contact("Child Contact", phone="+3456123", org=self.child_org)

        # add some fields
        parent_field = self.create_field("age", "Parent Age", org=self.parent_org)
        parent_datetime_field = self.create_field(
            "planting_date", "Planting Date", value_type=ContactField.TYPE_DATETIME, org=self.parent_org
        )
        child_field = self.create_field("age", "Child Age", org=self.child_org)

        # add some groups
        parent_group = self.create_group("Parent Customers", contacts=[parent_contact], org=self.parent_org)
        child_group = self.create_group("Parent Customers", contacts=[child_contact], org=self.child_org)

        # create an import for child group
        im = ContactImport.objects.create(
            org=self.org, group=child_group, mappings={}, num_records=0, created_by=self.admin, modified_by=self.admin
        )

        # and a batch for that import
        ContactImportBatch.objects.create(contact_import=im, specs={}, record_start=0, record_end=0)

        # add some labels
        parent_label = self.create_label("Parent Spam", org=self.parent_org)
        child_label = self.create_label("Child Spam", org=self.child_org)

        # bring in some flows
        parent_flow = self.get_flow("color_v13")
        flow_nodes = parent_flow.get_definition()["nodes"]
        (
            MockSessionWriter(parent_contact, parent_flow)
            .visit(flow_nodes[0])
            .send_msg("What is your favorite color?", self.channel)
            .visit(flow_nodes[4])
            .wait()
            .resume(msg=self.create_incoming_msg(parent_contact, "blue"))
            .set_result("Color", "blue", "Blue", "blue")
            .complete()
            .save()
        )
        parent_flow.channel_dependencies.add(self.channel)

        # and our child org too
        self.org = self.child_org
        child_flow = self.get_flow("color")

        FlowRun.objects.create(org=self.org, flow=child_flow, contact=child_contact)

        # labels for our flows
        flow_label1 = FlowLabel.create(self.parent_org, "Cool Parent Flows")
        flow_label2 = FlowLabel.create(self.child_org, "Cool Child Flows", parent=flow_label1)
        parent_flow.labels.add(flow_label1)
        child_flow.labels.add(flow_label2)

        # add a campaign, event and fire to our parent org
        campaign = Campaign.create(self.parent_org, self.admin, "Reminders", parent_group)
        event1 = CampaignEvent.create_flow_event(
            self.parent_org,
            self.admin,
            campaign,
            parent_datetime_field,
            offset=1,
            unit="W",
            flow=parent_flow,
            delivery_hour="13",
        )
        EventFire.objects.create(event=event1, contact=parent_contact, scheduled=timezone.now())

        # triggers for our flows
        parent_trigger = Trigger.create(
            self.parent_org,
            flow=parent_flow,
            trigger_type=Trigger.TYPE_KEYWORD,
            user=self.user,
            channel=self.channel,
            keyword="favorites",
        )
        parent_trigger.groups.add(self.parent_org.all_groups.all().first())

        FlowStart.objects.create(org=self.parent_org, flow=parent_flow)

        child_trigger = Trigger.create(
            self.child_org,
            flow=child_flow,
            trigger_type=Trigger.TYPE_KEYWORD,
            user=self.user,
            channel=self.child_channel,
            keyword="color",
        )
        child_trigger.groups.add(self.child_org.all_groups.all().first())

        # use a credit on each
        self.create_outgoing_msg(parent_contact, "Hola hija!", channel=self.channel)
        self.create_outgoing_msg(child_contact, "Hola mama!", channel=self.child_channel)

        # create a broadcast and some counts
        bcast1 = self.create_broadcast(self.user, "Broadcast with messages", contacts=[parent_contact])
        self.create_broadcast(self.user, "Broadcast with messages", contacts=[parent_contact], parent=bcast1)

        # create some archives
        self.mock_s3 = MockS3Client()

        # make some exports with logs
        export = ExportFlowResultsTask.create(
            self.parent_org, self.admin, [parent_flow], [parent_field], True, True, (), ()
        )
        Notification.export_finished(export)
        ExportFlowResultsTask.create(self.child_org, self.admin, [child_flow], [child_field], True, True, (), ())

        export = ExportContactsTask.create(self.parent_org, self.admin, group=parent_group)
        Notification.export_finished(export)
        ExportContactsTask.create(self.child_org, self.admin, group=child_group)

        export = ExportMessagesTask.create(self.parent_org, self.admin, label=parent_label, groups=[parent_group])
        Notification.export_finished(export)
        ExportMessagesTask.create(self.child_org, self.admin, label=child_label, groups=[child_group])

        def create_archive(org, period, rollup=None):
            file = f"{org.id}/archive{Archive.objects.all().count()}.jsonl.gz"
            body, md5, size = jsonlgz_encode([{"id": 1}])
            archive = Archive.objects.create(
                org=org,
                url=f"http://{settings.ARCHIVE_BUCKET}.aws.com/{file}",
                start_date=timezone.now(),
                build_time=100,
                archive_type=Archive.TYPE_MSG,
                period=period,
                rollup=rollup,
                size=size,
                hash=md5,
            )
            self.mock_s3.put_object(settings.ARCHIVE_BUCKET, file, body)
            return archive

        # parent archives
        daily = create_archive(self.parent_org, Archive.PERIOD_DAILY)
        create_archive(self.parent_org, Archive.PERIOD_MONTHLY, daily)

        # child archives
        daily = create_archive(self.child_org, Archive.PERIOD_DAILY)
        create_archive(self.child_org, Archive.PERIOD_MONTHLY, daily)

        # extra S3 file in child archive dir
        self.mock_s3.put_object(settings.ARCHIVE_BUCKET, f"{self.child_org.id}/extra_file.json", io.StringIO("[]"))

        # add a ticketer and ticket
        ticketer = Ticketer.create(self.org, self.admin, MailgunType.slug, "Email (bob)", {})
        ticket = self.create_ticket(ticketer, self.org.contacts.first(), "Help")
        ticket.events.create(org=self.org, contact=ticket.contact, event_type="N", note="spam", created_by=self.admin)

        # make sure we don't have any uncredited topups
        self.parent_org.apply_topups()

    def release_org(self, org, child_org=None, delete=False, expected_files=3):

        with patch("temba.utils.s3.client", return_value=self.mock_s3):
            # save off the ids of our current users
            org_user_ids = list(org.get_users().values_list("id", flat=True))

            # we should be starting with some mock s3 objects
            self.assertEqual(5, len(self.mock_s3.objects))

            # add in some webhook results
            resthook = Resthook.get_or_create(org, "registration", self.admin)
            resthook.subscribers.create(target_url="http://foo.bar", created_by=self.admin, modified_by=self.admin)
            WebHookEvent.objects.create(org=org, resthook=resthook, data={})

            TemplateTranslation.get_or_create(
                self.channel,
                "hello",
                "eng",
                "US",
                "Hello {{1}}",
                1,
                TemplateTranslation.STATUS_APPROVED,
                "1234",
                "foo_namespace",
            )

            # release our primary org
            org.release(self.superuser)
            if delete:
                org.delete()

            # all our users not in the other org should be inactive
            self.assertEqual(len(org_user_ids) - 1, User.objects.filter(id__in=org_user_ids, is_active=False).count())
            self.assertEqual(1, User.objects.filter(id__in=org_user_ids, is_active=True).count())

            # our child org lost it's parent, but maintains an active lifestyle
            if child_org:
                child_org.refresh_from_db()
                self.assertIsNone(child_org.parent)

            if delete:
                # oh noes, we deleted our archive files!
                self.assertEqual(expected_files, len(self.mock_s3.objects))

                # no template translations
                self.assertFalse(TemplateTranslation.objects.filter(template__org=org).exists())
                self.assertFalse(Template.objects.filter(org=org).exists())

                # our channels are gone too
                self.assertFalse(Channel.objects.filter(org=org).exists())

                # as are our webhook events
                self.assertFalse(WebHookEvent.objects.filter(org=org).exists())

                # and labels
                self.assertFalse(Label.all_objects.filter(org=org).exists())

                # contacts, groups
                self.assertFalse(Contact.objects.filter(org=org).exists())
                self.assertFalse(ContactGroup.all_groups.filter(org=org).exists())

                # flows, campaigns
                self.assertFalse(Flow.objects.filter(org=org).exists())
                self.assertFalse(Campaign.objects.filter(org=org).exists())

                # msgs, broadcasts
                self.assertFalse(Msg.objects.filter(org=org).exists())
                self.assertFalse(Broadcast.objects.filter(org=org).exists())

                # org is still around but has been released
                self.assertTrue(Org.objects.filter(id=org.id, is_active=False).exclude(deleted_on=None).exists())
            else:

                org.refresh_from_db()
                self.assertIsNone(org.deleted_on)
                self.assertFalse(org.is_active)

                # our channel should have been made inactive
                self.assertFalse(Channel.objects.filter(org=org, is_active=True).exists())
                self.assertTrue(Channel.objects.filter(org=org, is_active=False).exists())

    def test_release_parent(self):
        self.release_org(self.parent_org, self.child_org)

    def test_release_child(self):
        self.release_org(self.child_org)

    def test_release_parent_and_delete(self):
        with patch("temba.mailroom.client.MailroomClient.ticket_close"):
            self.release_org(self.parent_org, self.child_org, delete=True)

    def test_release_child_and_delete(self):
        # 300 credits were given to our child org and each used one
        self.assertEqual(695, self.parent_org.get_credits_remaining())
        self.assertEqual(299, self.child_org.get_credits_remaining())

        # release our child org
        self.release_org(self.child_org, delete=True, expected_files=2)

        # our unused credits are returned to the parent
        self.parent_org.clear_credit_cache()
        self.assertEqual(994, self.parent_org.get_credits_remaining())

    def test_delete_task(self):
        # can't delete an unreleased org
        with self.assertRaises(AssertionError):
            self.child_org.delete()

        self.release_org(self.child_org, delete=False)

        self.child_org.refresh_from_db()
        self.assertFalse(self.child_org.is_active)
        self.assertIsNotNone(self.child_org.released_on)
        self.assertIsNone(self.child_org.deleted_on)

        # push the released on date back in time
        Org.objects.filter(id=self.child_org.id).update(released_on=timezone.now() - timedelta(days=10))

        with patch("temba.utils.s3.client", return_value=self.mock_s3):
            delete_orgs_task()

        self.child_org.refresh_from_db()
        self.assertFalse(self.child_org.is_active)
        self.assertIsNotNone(self.child_org.released_on)
        self.assertIsNotNone(self.child_org.deleted_on)

        # parent org unaffected
        self.parent_org.refresh_from_db()
        self.assertTrue(self.parent_org.is_active)
        self.assertIsNone(self.parent_org.released_on)
        self.assertIsNone(self.parent_org.deleted_on)

        # can't double delete an org
        with self.assertRaises(AssertionError):
            self.child_org.delete()


class OrgTest(TembaTest):
    def test_get_users(self):
        # should return all org users
        self.assertEqual({self.admin, self.editor, self.user, self.agent, self.surveyor}, set(self.org.get_users()))

        # can filter by roles
        self.assertEqual({self.agent, self.editor}, set(self.org.get_users(roles=[OrgRole.EDITOR, OrgRole.AGENT])))

        # can get users with a specific permission
        self.assertEqual(
            {self.admin, self.agent, self.editor}, set(self.org.get_users_with_perm("tickets.ticket_assignee"))
        )

    def test_get_owner(self):
        # admins take priority
        self.assertEqual(self.admin, self.org.get_owner())

        self.org.administrators.clear()

        # then editors etc
        self.assertEqual(self.editor, self.org.get_owner())

        self.org.editors.clear()
        self.org.viewers.clear()
        self.org.agents.clear()
        self.org.surveyors.clear()

        # finally defaulting to org creator
        self.assertEqual(self.user, self.org.get_owner())

    def test_get_unique_slug(self):
        self.org.slug = "allo"
        self.org.save()

        self.assertEqual(Org.get_unique_slug("foo"), "foo")
        self.assertEqual(Org.get_unique_slug("Which part?"), "which-part")
        self.assertEqual(Org.get_unique_slug("Allo"), "allo-2")

    def test_set_flow_languages(self):
        self.assertEqual([], self.org.flow_languages)

        self.org.set_flow_languages(self.admin, ["eng", "fra"])
        self.org.refresh_from_db()
        self.assertEqual(["eng", "fra"], self.org.flow_languages)

        self.org.set_flow_languages(self.admin, ["kin", "eng"])
        self.org.refresh_from_db()
        self.assertEqual(["kin", "eng"], self.org.flow_languages)

        with self.assertRaises(AssertionError):
            self.org.set_flow_languages(self.admin, ["eng", "xyz"])
        with self.assertRaises(AssertionError):
            self.org.set_flow_languages(self.admin, ["eng", "eng"])

    def test_country_view(self):
        self.setUpLocations()

        home_url = reverse("orgs.org_home")
        country_url = reverse("orgs.org_country")

        rwanda = AdminBoundary.objects.get(name="Rwanda")

        # can't see this page if not logged in
        self.assertLoginRedirect(self.client.get(country_url))

        # login as admin instead
        self.login(self.admin)
        response = self.client.get(country_url)
        self.assertEqual(200, response.status_code)

        # save with Rwanda as a country
        self.client.post(country_url, {"country": rwanda.id})

        # assert it has changed
        self.org.refresh_from_db()
        self.assertEqual("Rwanda", str(self.org.country))
        self.assertEqual("RW", self.org.default_country_code)

        response = self.client.get(home_url)
        self.assertContains(response, "Rwanda")

        # if location support is disabled in the branding, don't display country formax
        current_branding = settings.BRANDING["rapidpro.io"]
        with override_settings(BRANDING={"rapidpro.io": {**current_branding, "location_support": False}}):
            response = self.client.get(home_url)
            self.assertNotContains(response, "Rwanda")

    def test_default_country(self):
        # if country boundary is set and name is valid country, that has priority
        self.org.country = AdminBoundary.create(osm_id="171496", name="Ecuador", level=0)
        self.org.timezone = "Africa/Nairobi"
        self.org.save(update_fields=("country", "timezone"))

        self.assertEqual("EC", self.org.default_country.alpha_2)

        del self.org.default_country

        # if country name isn't valid, we'll try timezone
        self.org.country.name = "Fantasia"
        self.org.country.save(update_fields=("name",))

        self.assertEqual("KE", self.org.default_country.alpha_2)

        del self.org.default_country

        # not all timezones have countries in which case we look at channels
        self.org.timezone = "UTC"
        self.org.save(update_fields=("timezone",))

        self.assertEqual("RW", self.org.default_country.alpha_2)

        del self.org.default_country

        # but if we don't have any channels.. no more backdowns
        self.org.channels.all().delete()

        self.assertIsNone(self.org.default_country)

    @patch("temba.utils.email.send_temba_email")
    def test_user_forget(self, mock_send_temba_email):

        invitation = Invitation.objects.create(
            org=self.org,
            user_group="A",
            email="invited@nyaruka.com",
            created_by=self.admin,
            modified_by=self.admin,
        )

        user = User.objects.create_user("existing@nyaruka.com", "existing@nyaruka.com")
        user.set_password("existing@nyaruka.com")
        user.save()

        forget_url = reverse("orgs.user_forget")
        smartmin_forget_url = reverse("users.user_forget")

        # make sure smartmin forget view is redirecting to our forget view
        response = self.client.get(smartmin_forget_url)
        self.assertEqual(301, response.status_code)
        self.assertEqual(response.url, forget_url)

        response = self.client.get(forget_url)
        self.assertEqual(200, response.status_code)

        post_data = dict(email="invited@nyaruka.com")

        response = self.client.post(forget_url, post_data, follow=True)
        self.assertEqual(200, response.status_code)

        email_args = mock_send_temba_email.call_args[0]  # all positional args

        self.assertEqual(email_args[0], "RapidPro Invitation")
        self.assertIn(f"https://app.rapidpro.io/org/join/{invitation.secret}/", email_args[1])
        self.assertNotIn("{{", email_args[1])
        self.assertIn(f"https://app.rapidpro.io/org/join/{invitation.secret}/", email_args[2])
        self.assertNotIn("{{", email_args[2])
        self.assertEqual(email_args[4], ["invited@nyaruka.com"])

        mock_send_temba_email.reset_mock()
        post_data = dict(email="existing@nyaruka.com")

        response = self.client.post(forget_url, post_data, follow=True)
        self.assertEqual(200, response.status_code)

        token_obj = RecoveryToken.objects.filter(user=user).first()

        email_args = mock_send_temba_email.call_args[0]  # all positional args
        self.assertEqual(email_args[0], "Password Recovery Request")
        self.assertIn(f"app.rapidpro.io/users/user/recover/{token_obj.token}/", email_args[1])
        self.assertNotIn("{{", email_args[1])
        self.assertIn(f"app.rapidpro.io/users/user/recover/{token_obj.token}/", email_args[2])
        self.assertNotIn("{{", email_args[2])
        self.assertEqual(email_args[4], ["existing@nyaruka.com"])

    def test_user_update(self):
        update_url = reverse("orgs.user_edit")
        login_url = reverse("users.user_login")

        # no access if anonymous
        response = self.client.get(update_url)
        self.assertRedirect(response, login_url)

        self.login(self.admin)

        # change the user language
        post_data = dict(
            language="pt-br",
            first_name="Admin",
            last_name="User",
            email="administrator@temba.com",
            current_password="Administrator",
        )
        response = self.client.post(update_url, post_data, HTTP_X_FORMAX=True)
        self.assertEqual(200, response.status_code)

        # check that our user settings have changed
        user_settings = self.admin.get_settings()
        self.assertEqual("pt-br", user_settings.language)

    @patch("temba.flows.models.FlowStart.async_start")
    def test_org_flagging_and_suspending(self, mock_async_start):
        self.login(self.admin)

        mark = self.create_contact("Mark", phone="+12065551212")
        flow = self.create_flow()

        def send_broadcast_via_api():
            url = reverse("api.v2.broadcasts")
            data = dict(contacts=[mark.uuid], text="You are a distant cousin to a wealthy person.")
            return self.client.post(
                url + ".json", json.dumps(data), content_type="application/json", HTTP_X_FORWARDED_HTTPS="https"
            )

        def start_flow_via_api():
            url = reverse("api.v2.flow_starts")
            data = dict(flow=flow.uuid, urns=["tel:+250788123123"])
            return self.client.post(
                url + ".json", json.dumps(data), content_type="application/json", HTTP_X_FORWARDED_HTTPS="https"
            )

        self.org.flag()
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_flagged)

        expected_message = "Sorry, your workspace is currently flagged. To re-enable starting flows and sending messages, please contact support."

        # while we are flagged, we can't send broadcasts
        response = self.client.get(reverse("msgs.broadcast_send"))
        self.assertContains(response, expected_message)

        # we also can't start flows
        response = self.client.get(reverse("flows.flow_broadcast", args=[flow.id]))
        self.assertContains(response, expected_message)

        response = send_broadcast_via_api()
        self.assertContains(response, expected_message, status_code=400)

        response = start_flow_via_api()
        self.assertContains(response, expected_message, status_code=400)

        # unflag org and suspend it instead
        self.org.unflag()
        self.org.is_suspended = True
        self.org.save(update_fields=("is_suspended",))

        expected_message = "Sorry, your workspace is currently suspended. To re-enable starting flows and sending messages, please contact support."

        response = self.client.get(reverse("msgs.broadcast_send"))
        self.assertContains(response, expected_message)

        # we also can't start flows
        response = self.client.get(reverse("flows.flow_broadcast", args=[flow.id]))
        self.assertContains(response, expected_message)

        response = send_broadcast_via_api()
        self.assertContains(response, expected_message, status_code=400)

        response = start_flow_via_api()
        self.assertContains(response, expected_message, status_code=400)

        # check our inbox page
        response = self.client.get(reverse("msgs.msg_inbox"))
        self.assertContains(response, "Your workspace is suspended")

        # still no messages or flow starts
        self.assertEqual(Msg.objects.all().count(), 0)
        mock_async_start.assert_not_called()

        # unsuspend our org and start a flow
        self.org.is_suspended = False
        self.org.save(update_fields=("is_suspended",))

        self.client.post(
            reverse("flows.flow_broadcast", args=[flow.id]),
            {"mode": "select", "omnibox": json.dumps({"id": mark.uuid, "name": mark.name, "type": "contact"})},
        )

        mock_async_start.assert_called_once()

    def test_accounts(self):
        url = reverse("orgs.org_accounts")
        self.login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "If you use the RapidPro Surveyor application to run flows offline")

        Org.objects.create(
            name="Another Org",
            timezone="Africa/Kigali",
            brand="rapidpro.io",
            created_by=self.user,
            modified_by=self.user,
            surveyor_password="nyaruka",
        )

        response = self.client.post(url, dict(surveyor_password="nyaruka"))
        self.org.refresh_from_db()
        self.assertContains(response, "This password is not valid. Choose a new password and try again.")
        self.assertIsNone(self.org.surveyor_password)

        # now try again, but with a unique password
        response = self.client.post(url, dict(surveyor_password="unique password"))
        self.org.refresh_from_db()
        self.assertEqual("unique password", self.org.surveyor_password)

        # add an extra editor
        editor = self.create_user("EditorTwo")
        self.org.editors.add(editor)
        self.surveyor.delete()

        # fetch it as a formax so we can inspect the summary
        response = self.client.get(url, HTTP_X_FORMAX=1, HTTP_X_PJAX=1)
        self.assertContains(response, "1 Administrator, 2 Editors, 1 Viewer, and 1 Agent.")

    def test_refresh_tokens(self):
        self.login(self.admin)
        url = reverse("orgs.org_home")
        response = self.client.get(url)

        # admin should have a token
        token = APIToken.objects.get(user=self.admin)

        # and it should be on the page
        self.assertContains(response, token.key)

        # let's refresh it
        self.client.post(reverse("api.apitoken_refresh"))

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
        response = self.client.post(reverse("api.apitoken_refresh"))
        self.assertLoginRedirect(response)

        # or just not an org user
        self.login(self.non_org_user)
        response = self.client.post(reverse("api.apitoken_refresh"))
        self.assertRedirect(response, reverse("orgs.org_choose"))

        # but can see it as an editor
        self.login(self.editor)
        response = self.client.get(url)
        token = APIToken.objects.get(user=self.editor)
        self.assertContains(response, token.key)

    @override_settings(SEND_EMAILS=True)
    def test_manage_accounts(self):
        url = reverse("orgs.org_manage_accounts")

        # can't access as editor
        self.login(self.editor)
        response = self.client.get(url)
        self.assertLoginRedirect(response)

        # can access as admin
        self.login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            [("A", "Administrator"), ("E", "Editor"), ("V", "Viewer"), ("T", "Agent"), ("S", "Surveyor")],
            response.context["form"].fields["invite_role"].choices,
        )

        # give users an API token and give admin and editor an additional surveyor-role token
        APIToken.get_or_create(self.org, self.admin)
        APIToken.get_or_create(self.org, self.editor)
        APIToken.get_or_create(self.org, self.surveyor)
        APIToken.get_or_create(self.org, self.admin, role=Group.objects.get(name="Surveyors"))
        APIToken.get_or_create(self.org, self.editor, role=Group.objects.get(name="Surveyors"))

        actual_fields = response.context["form"].fields
        expected_fields = ["loc", "invite_emails", "invite_role"]
        for user in self.org.get_users():
            expected_fields.extend([f"user_{user.id}_role", f"user_{user.id}_remove"])

        self.assertEqual(set(expected_fields), set(actual_fields.keys()))

        self.assertEqual("A", actual_fields[f"user_{self.admin.id}_role"].initial)
        self.assertEqual("E", actual_fields[f"user_{self.editor.id}_role"].initial)
        self.assertEqual(None, actual_fields["invite_emails"].initial)
        self.assertEqual("V", actual_fields["invite_role"].initial)

        # leave admin, editor and agent as is, but change user to an editor too, and remove the surveyor user
        response = self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.editor.id}_role": "E",
                f"user_{self.user.id}_role": "E",
                f"user_{self.surveyor.id}_role": "S",
                f"user_{self.surveyor.id}_remove": "1",
                f"user_{self.agent.id}_role": "T",
                "invite_emails": "",
                "invite_role": "V",
            },
        )
        self.assertRedirect(response, reverse("orgs.org_manage_accounts"))

        self.org.refresh_from_db()
        self.assertEqual(set(self.org.administrators.all()), {self.admin})
        self.assertEqual(set(self.org.editors.all()), {self.user, self.editor})
        self.assertFalse(set(self.org.viewers.all()), set())
        self.assertEqual(set(self.org.surveyors.all()), set())
        self.assertEqual(set(self.org.agents.all()), {self.agent})

        # our surveyor's API token will have been deleted
        self.assertEqual(self.admin.api_tokens.filter(is_active=True).count(), 2)
        self.assertEqual(self.editor.api_tokens.filter(is_active=True).count(), 2)
        self.assertEqual(self.surveyor.api_tokens.filter(is_active=True).count(), 0)

        # next we leave existing roles unchanged, but try to invite new user to be admin with an invalid email address
        response = self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.editor.id}_role": "E",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "T",
                "invite_emails": "norkans7gmail.com",
                "invite_role": "A",
            },
        )
        self.assertFormError(response, "form", "invite_emails", "One of the emails you entered is invalid.")

        # try again with valid email
        response = self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.editor.id}_role": "E",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "T",
                "invite_emails": "norkans7@gmail.com",
                "invite_role": "A",
            },
        )
        self.assertRedirect(response, reverse("orgs.org_manage_accounts"))

        # an invitation is created
        invitation = Invitation.objects.get()
        self.assertEqual(self.org, invitation.org)
        self.assertEqual("norkans7@gmail.com", invitation.email)
        self.assertEqual("A", invitation.user_group)

        # and sent by email
        self.assertEqual(1, len(mail.outbox))

        # pretend our invite was acted on
        invitation.is_active = False
        invitation.save()

        # no longer appears in list
        response = self.client.get(url)
        self.assertNotContains(response, "norkans7@gmail.com")

        # include multiple emails on the form
        self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.editor.id}_role": "E",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "T",
                "invite_emails": "norbert@temba.com,code@temba.com",
                "invite_role": "A",
            },
        )

        # now 2 new invitations are created and sent
        self.assertEqual(3, Invitation.objects.all().count())
        self.assertEqual(3, len(mail.outbox))

        response = self.client.get(url)

        # user ordered by email
        users_on_form = [row["user"] for row in response.context["form"].user_rows]
        self.assertEqual([self.admin, self.agent, self.editor, self.user], users_on_form)

        # invites ordered by email as well
        invites_on_form = [row["invite"].email for row in response.context["form"].invite_rows]
        self.assertEqual(["code@temba.com", "norbert@temba.com"], invites_on_form)

        # users for whom nothing is submitted for remain unchanged
        response = self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "A",
                "invite_emails": "",
                "invite_role": "A",
            },
        )
        self.assertEqual(200, response.status_code)

        self.org.refresh_from_db()
        self.assertEqual(set(self.org.administrators.all()), {self.admin})
        self.assertEqual(set(self.org.editors.all()), {self.user, self.editor})
        self.assertFalse(set(self.org.viewers.all()), set())
        self.assertEqual(set(self.org.surveyors.all()), set())
        self.assertEqual(set(self.org.agents.all()), {self.agent})

        # try to remove ourselves as admin
        response = self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.admin.id}_remove": "1",
                f"user_{self.editor.id}_role": "S",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "T",
                "invite_emails": "",
                "invite_role": "V",
            },
        )
        self.assertFormError(response, "form", "__all__", "A workspace must have at least one administrator.")

        # try to downgrade ourselves to an editor
        response = self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "E",
                f"user_{self.editor.id}_role": "S",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "T",
                "invite_emails": "",
                "invite_role": "V",
            },
        )
        self.assertFormError(response, "form", "__all__", "A workspace must have at least one administrator.")

        # finally upgrade agent to admin, downgrade editor to surveyor, remove ourselves entirely and remove last invite
        last_invite = Invitation.objects.last()
        response = self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.admin.id}_remove": "1",
                f"user_{self.editor.id}_role": "S",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "A",
                f"invite_{last_invite.id}_remove": "1",
                "invite_emails": "",
                "invite_role": "V",
            },
        )

        # we should be redirected to chooser page
        self.assertRedirect(response, reverse("orgs.org_choose"))

        self.assertEqual(2, Invitation.objects.all().count())

        # and removed from this org
        self.org.refresh_from_db()
        self.assertEqual(set(self.org.administrators.all()), {self.agent})
        self.assertEqual(set(self.org.editors.all()), {self.user})
        self.assertEqual(set(self.org.viewers.all()), set())
        self.assertEqual(set(self.org.surveyors.all()), {self.editor})
        self.assertEqual(set(self.org.agents.all()), set())

        # editor will have lost their editor API token, but not their surveyor token
        self.editor.refresh_from_db()
        self.assertEqual([t.role.name for t in self.editor.api_tokens.filter(is_active=True)], ["Surveyors"])

        # and all our API tokens for the admin are deleted
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.api_tokens.filter(is_active=True).count(), 0)

        # make sure an existing user can not be invited again
        user = Org.create_user("admin1@temba.com", "admin1@temba.com")
        user.set_org(self.org)
        self.org.administrators.add(user)
        self.login(user)

        self.assertEqual(1, Invitation.objects.filter(is_active=True).count())

        # include multiple emails on the form
        response = self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.editor.id}_role": "E",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "T",
                f"user_{user.id}_role": "A",
                "invite_emails": "norbert@temba.com,code@temba.com,admin1@temba.com",
                "invite_role": "A",
            },
        )

        self.assertFormError(
            response, "form", "invite_emails", "One of the emails you entered has an existing user on the workspace."
        )

        # do not allow multiple invite on the same email
        response = self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.editor.id}_role": "E",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "T",
                f"user_{user.id}_role": "A",
                "invite_emails": "norbert@temba.com,code@temba.com",
                "invite_role": "A",
            },
        )

        self.assertFormError(
            response, "form", "invite_emails", "One of the emails you entered has an existing user on the workspace."
        )

        # no error for inactive invite
        response = self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.editor.id}_role": "E",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "T",
                f"user_{user.id}_role": "A",
                "invite_emails": "code@temba.com, code@temba.com",
                "invite_role": "A",
            },
        )

        self.assertFormError(response, "form", "invite_emails", "One of the emails you entered is duplicated.")

        # no error for inactive invite
        response = self.client.post(
            url,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.editor.id}_role": "E",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "T",
                f"user_{user.id}_role": "A",
                "invite_emails": "code@temba.com",
                "invite_role": "A",
            },
        )

        self.assertEqual(2, Invitation.objects.filter(is_active=True).count())
        self.assertTrue(Invitation.objects.filter(is_active=True, email="code@temba.com").exists())
        self.assertEqual(4, len(mail.outbox))

    @patch("temba.utils.email.send_temba_email")
    def test_join(self, mock_send_temba_email):
        def create_invite(group, username):
            return Invitation.objects.create(
                org=self.org,
                user_group=group,
                email=f"{username}@nyaruka.com",
                created_by=self.admin,
                modified_by=self.admin,
            )

        def create_user(username):
            user = User.objects.create_user(f"{username}@nyaruka.com", f"{username}@nyaruka.com")
            user.set_password(f"{username}@nyaruka.com")
            user.save()
            return user

        editor_invitation = create_invite("E", "invitededitor")
        editor_invitation.send()
        email_args = mock_send_temba_email.call_args[0]  # all positional args

        self.assertEqual(email_args[0], "RapidPro Invitation")
        self.assertIn("https://app.rapidpro.io/org/join/%s/" % editor_invitation.secret, email_args[1])
        self.assertNotIn("{{", email_args[1])
        self.assertIn("https://app.rapidpro.io/org/join/%s/" % editor_invitation.secret, email_args[2])
        self.assertNotIn("{{", email_args[2])

        editor_join_url = reverse("orgs.org_join", args=[editor_invitation.secret])
        self.client.logout()

        # if no user is logged we redirect to the create_login page
        response = self.client.get(editor_join_url)
        self.assertEqual(302, response.status_code)
        response = self.client.get(editor_join_url, follow=True)
        self.assertEqual(
            response.request["PATH_INFO"], reverse("orgs.org_create_login", args=[editor_invitation.secret])
        )

        # a user is already logged in
        self.invited_editor = create_user("invitededitor")

        # different user login
        self.login(self.admin)

        response = self.client.get(editor_join_url)
        self.assertEqual(200, response.status_code)

        # should be logged out to request login
        self.assertEqual(0, len(self.client.session.keys()))

        # login with a diffent user that the invited
        self.login(self.admin)
        response = self.client.get(reverse("orgs.org_join_accept", args=[editor_invitation.secret]), follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("orgs.org_join", args=[editor_invitation.secret]))

        self.login(self.invited_editor)
        response = self.client.get(editor_join_url)
        self.assertEqual(302, response.status_code)
        response = self.client.get(editor_join_url, follow=True)
        self.assertEqual(
            response.request["PATH_INFO"], reverse("orgs.org_join_accept", args=[editor_invitation.secret])
        )

        editor_join_accept_url = reverse("orgs.org_join_accept", args=[editor_invitation.secret])
        self.login(self.invited_editor)

        response = self.client.get(editor_join_accept_url)
        self.assertEqual(200, response.status_code)

        self.assertEqual(self.org.pk, response.context["org"].pk)
        # we have a form without field except one 'loc'
        self.assertEqual(1, len(response.context["form"].fields))

        post_data = dict()
        response = self.client.post(editor_join_accept_url, post_data, follow=True)
        self.assertEqual(200, response.status_code)

        self.assertIn(self.invited_editor, self.org.editors.all())
        self.assertFalse(Invitation.objects.get(pk=editor_invitation.pk).is_active)

        roles = (
            ("V", self.org.viewers),
            ("S", self.org.surveyors),
            ("A", self.org.administrators),
            ("E", self.org.editors),
        )

        # test it for each role
        for role in roles:
            invite = create_invite(role[0], "User%s" % role[0])
            user = create_user("User%s" % role[0])
            self.login(user)
            response = self.client.post(reverse("orgs.org_join_accept", args=[invite.secret]), follow=True)
            self.assertEqual(200, response.status_code)
            self.assertIsNotNone(role[1].filter(pk=user.pk).first())

        # try an expired invite
        invite = create_invite("S", "invitedexpired")
        invite.is_active = False
        invite.save()
        expired_user = create_user("invitedexpired")
        self.login(expired_user)
        response = self.client.post(reverse("orgs.org_join_accept", args=[invite.secret]), follow=True)
        self.assertEqual(200, response.status_code)
        self.assertIsNone(self.org.surveyors.filter(pk=expired_user.pk).first())

        response = self.client.post(reverse("orgs.org_join", args=[invite.secret]))
        self.assertEqual(302, response.status_code)

        response = self.client.post(reverse("orgs.org_join", args=[invite.secret]), follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("users.user_login"))

    def test_create_login(self):
        admin_invitation = Invitation.objects.create(
            org=self.org, user_group="A", email="norkans7@gmail.com", created_by=self.admin, modified_by=self.admin
        )

        admin_create_login_url = reverse("orgs.org_create_login", args=[admin_invitation.secret])
        self.client.logout()

        response = self.client.get(admin_create_login_url)
        self.assertEqual(200, response.status_code)

        self.assertEqual(self.org.pk, response.context["org"].pk)

        # we have a form with 3 fields and one hidden 'loc'
        self.assertEqual(4, len(response.context["form"].fields))
        self.assertIn("first_name", response.context["form"].fields)
        self.assertIn("last_name", response.context["form"].fields)
        self.assertIn("password", response.context["form"].fields)

        post_data = dict()
        post_data["first_name"] = "Norbert"
        post_data["last_name"] = "Kwizera"
        post_data["password"] = "norbertkwizeranorbert"

        response = self.client.post(admin_create_login_url, post_data, follow=True)
        self.assertEqual(200, response.status_code)

        new_invited_user = User.objects.get(email="norkans7@gmail.com")
        self.assertTrue(new_invited_user in self.org.administrators.all())
        self.assertFalse(Invitation.objects.get(pk=admin_invitation.pk).is_active)

        invitation = Invitation.objects.create(
            org=self.org, user_group="E", email="norkans7@gmail.com", created_by=self.admin, modified_by=self.admin
        )
        create_login_url = reverse("orgs.org_create_login", args=[invitation.secret])

        # we have a matching user so we redirect with the user logged in
        response = self.client.get(create_login_url)
        self.assertEqual(302, response.status_code)

        response = self.client.get(create_login_url, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("orgs.org_join_accept", args=[invitation.secret]))

        invitation.is_active = False
        invitation.save()

        response = self.client.get(create_login_url)
        self.assertEqual(302, response.status_code)
        response = self.client.get(create_login_url, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("public.public_index"))

    def test_create_login_invalid_form(self):
        admin_invitation = Invitation.objects.create(
            org=self.org, user_group="A", email="user@example.com", created_by=self.admin, modified_by=self.admin
        )

        admin_create_login_url = reverse("orgs.org_create_login", args=[admin_invitation.secret])
        self.client.logout()

        response = self.client.post(
            admin_create_login_url,
            {
                "first_name": f"Ni{'c' * 150}",
                "last_name": f"Po{'t' * 150}ier",
                "password": "just-a-password",
            },
        )
        self.assertFormError(
            response, "form", "first_name", "Ensure this value has at most 150 characters (it has 152)."
        )
        self.assertFormError(
            response, "form", "last_name", "Ensure this value has at most 150 characters (it has 155)."
        )

    def test_surveyor_invite(self):
        surveyor_invite = Invitation.objects.create(
            org=self.org, user_group="S", email="surveyor@gmail.com", created_by=self.admin, modified_by=self.admin
        )

        admin_create_login_url = reverse("orgs.org_create_login", args=[surveyor_invite.secret])
        self.client.logout()

        response = self.client.post(
            admin_create_login_url,
            {"first_name": "Surveyor", "last_name": "User", "email": "surveyor@gmail.com", "password": "HeyThere123"},
            follow=True,
        )
        self.assertEqual(200, response.status_code)

        # as a surveyor we should have been rerouted
        self.assertEqual(reverse("orgs.org_surveyor"), response._request.path)
        self.assertFalse(Invitation.objects.get(pk=surveyor_invite.pk).is_active)

        # make sure we are a surveyor
        new_invited_user = User.objects.get(email="surveyor@gmail.com")
        self.assertIn(new_invited_user, self.org.surveyors.all())

        # if we login, we should be rerouted too
        self.client.logout()
        response = self.client.post(
            "/users/login/", {"username": "surveyor@gmail.com", "password": "HeyThere123"}, follow=True
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(reverse("orgs.org_surveyor"), response._request.path)

    def test_surveyor(self):
        self.client.logout()
        url = "%s?mobile=true" % reverse("orgs.org_surveyor")

        # try creating a surveyor account with a bogus password
        post_data = dict(surveyor_password="badpassword")
        response = self.client.post(url, post_data)
        self.assertContains(
            response, "Invalid surveyor password, please check with your project leader and try again."
        )

        # put a space in the org name to test URL encoding and set a surveyor password
        self.org.name = "Temba Org"
        self.org.surveyor_password = "nyaruka"
        self.org.save()

        # now lets try again
        post_data = dict(surveyor_password="nyaruka")
        response = self.client.post(url, post_data)
        self.assertContains(response, "Enter your details below to create your login.")

        # now try creating an account on the second step without and surveyor_password
        post_data = dict(
            first_name="Marshawn", last_name="Lynch", password="beastmode24", email="beastmode@seahawks.com"
        )
        response = self.client.post(url, post_data)
        self.assertContains(response, "Enter your details below to create your login.")

        # now do the same but with a valid surveyor_password
        post_data = dict(
            first_name="Marshawn",
            last_name="Lynch",
            password="beastmode24",
            email="beastmode@seahawks.com",
            surveyor_password="nyaruka",
        )
        response = self.client.post(url, post_data)
        self.assertIn("token", response.url)
        self.assertIn("beastmode", response.url)
        self.assertIn("Temba%20Org", response.url)

        # try with a login that already exists
        post_data = dict(
            first_name="Resused",
            last_name="Email",
            password="mypassword1",
            email="beastmode@seahawks.com",
            surveyor_password="nyaruka",
        )
        response = self.client.post(url, post_data)
        self.assertContains(response, "That email address is already used")

        # try with a login that already exists
        post_data = dict(
            first_name="Short",
            last_name="Password",
            password="short",
            email="thomasrawls@seahawks.com",
            surveyor_password="nyaruka",
        )
        response = self.client.post(url, post_data)
        self.assertFormError(
            response, "form", "password", "This password is too short. It must contain at least 8 characters."
        )

        # finally make sure our login works
        success = self.client.login(username="beastmode@seahawks.com", password="beastmode24")
        self.assertTrue(success)

        # and that we only have the surveyor role
        self.assertIsNotNone(self.org.surveyors.filter(username="beastmode@seahawks.com").first())
        self.assertIsNone(self.org.administrators.filter(username="beastmode@seahawks.com").first())
        self.assertIsNone(self.org.editors.filter(username="beastmode@seahawks.com").first())
        self.assertIsNone(self.org.viewers.filter(username="beastmode@seahawks.com").first())

    def test_topup_admin(self):
        self.login(self.admin)

        topup = self.org.topups.get()

        # admins shouldn't be able to see the create / manage / update pages
        manage_url = reverse("orgs.topup_manage") + "?org=%d" % self.org.id
        self.assertRedirect(self.client.get(manage_url), "/users/login/")

        create_url = reverse("orgs.topup_create") + "?org=%d" % self.org.id
        self.assertRedirect(self.client.get(create_url), "/users/login/")

        update_url = reverse("orgs.topup_update", args=[topup.pk])
        self.assertRedirect(self.client.get(update_url), "/users/login/")

        # log in as root
        self.login(self.superuser)

        # should list our one topup
        response = self.client.get(manage_url)
        self.assertEqual([topup], list(response.context["object_list"]))

        # create a new one
        response = self.client.post(create_url, {"price": "1000", "credits": "500", "comment": ""})
        self.assertEqual(302, response.status_code)

        self.assertEqual(2, self.org.topups.count())
        self.assertEqual(1500, self.org.get_credits_remaining())

        # update one of our topups
        response = self.client.post(
            update_url,
            {"is_active": True, "price": "0", "credits": "5000", "comment": "", "expires_on": "2025-04-03 13:47:46"},
        )
        self.assertEqual(302, response.status_code)

        self.assertEqual(5500, self.org.get_credits_remaining())

    def test_topup_model(self):
        topup = TopUp.create(self.admin, price=None, credits=1000)

        self.assertEqual(topup.get_price_display(), "")

        topup.price = 0
        topup.save()

        self.assertEqual(topup.get_price_display(), "Free")

        topup.price = 100
        topup.save()

        self.assertEqual(topup.get_price_display(), "$1.00")

        # ttl should never be negative even if expired
        topup.expires_on = timezone.now() - timedelta(days=1)
        topup.save(update_fields=["expires_on"])
        self.assertEqual(10, self.org.get_topup_ttl(topup))

    def test_topup_expiration(self):

        contact = self.create_contact("Usain Bolt", phone="+250788123123")
        welcome_topup = self.org.topups.get()

        # send some messages with a valid topup
        self.create_incoming_msgs(contact, 10)
        self.assertEqual(10, Msg.objects.filter(org=self.org, topup=welcome_topup).count())
        self.assertEqual(990, self.org.get_credits_remaining())

        # now expire our topup and try sending more messages
        welcome_topup.expires_on = timezone.now() - timedelta(hours=1)
        welcome_topup.save(update_fields=("expires_on",))
        self.org.clear_credit_cache()

        # we should have no credits remaining since we expired
        self.assertEqual(0, self.org.get_credits_remaining())
        self.create_incoming_msgs(contact, 5)

        # those messages are waiting to send
        self.assertEqual(5, Msg.objects.filter(org=self.org, topup=None).count())

        # so we should report -5 credits
        self.assertEqual(-5, self.org.get_credits_remaining())

        # our first 10 messages plus our 5 pending a topup
        self.assertEqual(15, self.org.get_credits_used())

    def test_low_credits_threshold(self):
        contact = self.create_contact("Usain Bolt", phone="+250788123123")

        # add some more unexpire topup credits
        TopUp.create(self.admin, price=0, credits=1000)
        TopUp.create(self.admin, price=0, credits=1000)
        TopUp.create(self.admin, price=0, credits=1000)

        # send some messages with a valid topup
        self.create_incoming_msgs(contact, 2200)

        self.assertEqual(300, self.org.get_low_credits_threshold())

    def test_topup_decrementing(self):
        self.contact = self.create_contact("Joe", phone="+250788123123")

        self.create_incoming_msg(self.contact, "Orange")

        # check our credits
        self.login(self.admin)
        response = self.client.get(reverse("orgs.org_home"))

        # We now show org plan
        # self.assertContains(response, "<b>999</b>")

        # view our topups
        response = self.client.get(reverse("orgs.topup_list"))

        # and that we have 999 credits left on our topup
        self.assertContains(response, "999\n")

        # should say we have a 1,000 credits too
        self.assertContains(response, "1,000 Credits")

        # our receipt should show that the topup was free
        with patch("stripe.Charge.retrieve") as stripe:
            stripe.return_value = ""
            response = self.client.get(
                reverse("orgs.topup_read", args=[TopUp.objects.filter(org=self.org).first().pk])
            )
            self.assertContains(response, "1,000 Credits")

    def test_topups(self):

        settings.BRANDING[settings.DEFAULT_BRAND]["tiers"] = dict(multi_user=100_000, multi_org=1_000_000)
        self.org.is_multi_org = False
        self.org.is_multi_user = False
        self.org.save(update_fields=["is_multi_user", "is_multi_org"])

        contact = self.create_contact("Michael Shumaucker", phone="+250788123123")
        welcome_topup = self.org.topups.get()

        self.create_incoming_msgs(contact, 10)

        with self.assertNumQueries(3):
            self.assertEqual(150, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEqual(150, self.org.get_low_credits_threshold())

        # we should have 1000 minus 10 credits for this org
        with self.assertNumQueries(5):
            self.assertEqual(990, self.org.get_credits_remaining())  # from db

        with self.assertNumQueries(0):
            self.assertEqual(1000, self.org.get_credits_total())  # from cache
            self.assertEqual(10, self.org.get_credits_used())
            self.assertEqual(990, self.org.get_credits_remaining())

        welcome_topup.refresh_from_db()
        self.assertEqual(10, welcome_topup.msgs.count())
        self.assertEqual(10, welcome_topup.get_used())

        # at this point we shouldn't have squashed any topup credits, so should have the same number as our used
        self.assertEqual(10, TopUpCredits.objects.all().count())

        # now squash
        squash_topupcredits()

        # should only have one remaining
        self.assertEqual(1, TopUpCredits.objects.all().count())

        # reduce our credits on our topup to 15
        TopUp.objects.filter(pk=welcome_topup.pk).update(credits=15)
        self.org.clear_credit_cache()

        self.assertEqual(15, self.org.get_credits_total())
        self.assertEqual(5, self.org.get_credits_remaining())

        # create 10 more messages, only 5 of which will get a topup
        self.create_incoming_msgs(contact, 10)

        welcome_topup.refresh_from_db()
        self.assertEqual(15, welcome_topup.msgs.count())
        self.assertEqual(15, welcome_topup.get_used())

        (topup, _) = self.org._calculate_active_topup()
        self.assertFalse(topup)

        # we generate queries for total and used when we are near a boundary
        with self.assertNumQueries(4):
            self.assertEqual(15, self.org.get_credits_total())
            self.assertEqual(20, self.org.get_credits_used())
            self.assertEqual(-5, self.org.get_credits_remaining())

        # again create 10 more messages, none of which will get a topup
        self.create_incoming_msgs(contact, 10)

        with self.assertNumQueries(0):
            self.assertEqual(15, self.org.get_credits_total())
            self.assertEqual(30, self.org.get_credits_used())
            self.assertEqual(-15, self.org.get_credits_remaining())

        self.assertEqual(15, TopUp.objects.get(pk=welcome_topup.pk).get_used())

        # run our check on topups, this should suspend our org
        suspend_topup_orgs_task()
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_suspended)
        self.assertTrue(timezone.now() - self.org.plan_end < timedelta(seconds=10))

        # raise our topup to take 20 and create another for 5
        TopUp.objects.filter(pk=welcome_topup.pk).update(credits=20)
        new_topup = TopUp.create(self.admin, price=0, credits=5)

        # apply topups which will max out both and reduce debt to 5
        self.org.apply_topups()

        self.assertEqual(20, welcome_topup.msgs.count())
        self.assertEqual(20, TopUp.objects.get(pk=welcome_topup.pk).get_used())
        self.assertEqual(5, new_topup.msgs.count())
        self.assertEqual(5, TopUp.objects.get(pk=new_topup.pk).get_used())
        self.assertEqual(25, self.org.get_credits_total())
        self.assertEqual(30, self.org.get_credits_used())
        self.assertEqual(-5, self.org.get_credits_remaining())
        self.assertTrue(self.org.is_suspended)

        # test special status
        self.assertFalse(self.org.is_multi_user)
        self.assertFalse(self.org.is_multi_org)

        # add new topup with lots of credits
        mega_topup = TopUp.create(self.admin, price=0, credits=100_000)

        # after applying this, no messages should be without a topup
        self.org.apply_topups()
        self.assertFalse(Msg.objects.filter(org=self.org, topup=None))
        self.assertEqual(5, TopUp.objects.get(pk=mega_topup.pk).get_used())

        # we aren't yet multi user since this topup was free
        self.assertEqual(0, self.org.get_purchased_credits())
        self.assertFalse(self.org.is_multi_user)

        self.assertEqual(100_025, self.org.get_credits_total())
        self.assertEqual(99995, self.org.get_credits_remaining())
        self.assertEqual(30, self.org.get_credits_used())
        self.assertFalse(self.org.is_suspended)

        # and new messages use the mega topup
        msg = self.create_incoming_msg(contact, "Test")
        self.assertEqual(msg.topup, mega_topup)
        self.assertEqual(6, TopUp.objects.get(pk=mega_topup.pk).get_used())

        # but now it expires
        yesterday = timezone.now() - relativedelta(days=1)
        mega_topup.expires_on = yesterday
        mega_topup.save(update_fields=["expires_on"])
        self.org.clear_credit_cache()

        # new incoming messages should not be assigned a topup
        msg = self.create_incoming_msg(contact, "Test")
        self.assertIsNone(msg.topup)

        # check our totals
        self.org.clear_credit_cache()

        with self.assertNumQueries(6):
            self.assertEqual(0, self.org.get_purchased_credits())
            self.assertEqual(31, self.org.get_credits_total())
            self.assertEqual(32, self.org.get_credits_used())
            self.assertEqual(-1, self.org.get_credits_remaining())

        # all top up expired
        TopUp.objects.all().update(expires_on=yesterday)

        # we have expiring credits, and no more active
        gift_topup = TopUp.create(self.admin, price=0, credits=100)
        next_week = timezone.now() + relativedelta(days=7)
        gift_topup.expires_on = next_week
        gift_topup.save(update_fields=["expires_on"])
        self.org.apply_topups()

        with self.assertNumQueries(3):
            self.assertEqual(15, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEqual(15, self.org.get_low_credits_threshold())

        # some credits expires but more credits will remain active
        later_active_topup = TopUp.create(self.admin, price=0, credits=200)
        five_week_ahead = timezone.now() + relativedelta(days=35)
        later_active_topup.expires_on = five_week_ahead
        later_active_topup.save(update_fields=["expires_on"])
        self.org.apply_topups()

        with self.assertNumQueries(4):
            self.assertEqual(45, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEqual(45, self.org.get_low_credits_threshold())

        # no expiring credits
        gift_topup.expires_on = five_week_ahead
        gift_topup.save(update_fields=["expires_on"])
        self.org.clear_credit_cache()

        with self.assertNumQueries(6):
            self.assertEqual(45, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEqual(45, self.org.get_low_credits_threshold())

        # do not consider expired topup
        gift_topup.expires_on = yesterday
        gift_topup.save(update_fields=["expires_on"])
        self.org.clear_credit_cache()

        with self.assertNumQueries(5):
            self.assertEqual(30, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEqual(30, self.org.get_low_credits_threshold())

        TopUp.objects.all().update(is_active=False)
        self.org.clear_credit_cache()

        with self.assertNumQueries(2):
            self.assertEqual(0, self.org.get_low_credits_threshold())

        with self.assertNumQueries(0):
            self.assertEqual(0, self.org.get_low_credits_threshold())

        # now buy some credits to make us multi user
        TopUp.create(self.admin, price=100, credits=100_000)
        self.org.clear_credit_cache()
        self.org.reset_capabilities()
        self.assertTrue(self.org.is_multi_user)
        self.assertFalse(self.org.is_multi_org)

        # good deal!
        TopUp.create(self.admin, price=100, credits=1_000_000)
        self.org.clear_credit_cache()
        self.org.reset_capabilities()
        self.assertTrue(self.org.is_multi_user)
        self.assertTrue(self.org.is_multi_org)

    @patch("temba.orgs.views.Client", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_twilio_connect(self):
        with patch("temba.tests.twilio.MockTwilioClient.MockAccounts.get") as mock_get:
            mock_get.return_value = MockTwilioClient.MockAccount("Full")

            connect_url = reverse("orgs.org_twilio_connect")

            self.login(self.admin)
            self.admin.set_org(self.org)

            response = self.client.get(connect_url)
            self.assertEqual(200, response.status_code)
            self.assertEqual(list(response.context["form"].fields.keys()), ["account_sid", "account_token", "loc"])

            # try posting without an account token
            post_data = {"account_sid": "AccountSid"}
            response = self.client.post(connect_url, post_data)
            self.assertFormError(response, "form", "account_token", "This field is required.")

            # now add the account token and try again
            post_data["account_token"] = "AccountToken"

            # but with an unexpected exception
            with patch("temba.tests.twilio.MockTwilioClient.__init__") as mock:
                mock.side_effect = Exception("Unexpected")
                response = self.client.post(connect_url, post_data)
                self.assertFormError(
                    response,
                    "form",
                    "__all__",
                    "The Twilio account SID and Token seem invalid. " "Please check them again and retry.",
                )

            self.client.post(connect_url, post_data)

            self.org.refresh_from_db()
            self.assertEqual(self.org.config["ACCOUNT_SID"], "AccountSid")
            self.assertEqual(self.org.config["ACCOUNT_TOKEN"], "AccountToken")

            # when the user submit the secondary token, we use it to get the primary one from the rest API
            with patch("temba.tests.twilio.MockTwilioClient.MockAccounts.get") as mock_get_primary:
                with patch("twilio.rest.api.v2010.account.AccountContext.fetch") as mock_account_fetch:
                    mock_get_primary.return_value = MockTwilioClient.MockAccount("Full", "PrimaryAccountToken")
                    mock_account_fetch.return_value = MockTwilioClient.MockAccount("Full", "PrimaryAccountToken")

                    response = self.client.post(connect_url, post_data)
                    self.assertEqual(response.status_code, 302)

                    response = self.client.post(connect_url, post_data, follow=True)
                    self.assertEqual(response.request["PATH_INFO"], reverse("channels.types.twilio.claim"))

                    self.org.refresh_from_db()
                    self.assertEqual(self.org.config["ACCOUNT_SID"], "AccountSid")
                    self.assertEqual(self.org.config["ACCOUNT_TOKEN"], "PrimaryAccountToken")

                    twilio_account_url = reverse("orgs.org_twilio_account")
                    response = self.client.get(twilio_account_url)
                    self.assertEqual("AccountSid", response.context["account_sid"])

                    self.org.refresh_from_db()
                    config = self.org.config
                    self.assertEqual("AccountSid", config["ACCOUNT_SID"])
                    self.assertEqual("PrimaryAccountToken", config["ACCOUNT_TOKEN"])

                    # post without a sid or token, should get a form validation error
                    response = self.client.post(twilio_account_url, dict(disconnect="false"), follow=True)
                    self.assertEqual(
                        '[{"message": "You must enter your Twilio Account SID", "code": ""}]',
                        response.context["form"].errors["__all__"].as_json(),
                    )

                    # all our twilio creds should remain the same
                    self.org.refresh_from_db()
                    config = self.org.config
                    self.assertEqual(config["ACCOUNT_SID"], "AccountSid")
                    self.assertEqual(config["ACCOUNT_TOKEN"], "PrimaryAccountToken")

                    # now try with all required fields, and a bonus field we shouldn't change
                    self.client.post(
                        twilio_account_url,
                        dict(
                            account_sid="AccountSid",
                            account_token="SecondaryToken",
                            disconnect="false",
                            name="DO NOT CHANGE ME",
                        ),
                        follow=True,
                    )
                    # name shouldn't change
                    self.org.refresh_from_db()
                    self.assertEqual(self.org.name, "Temba")

                    # now disconnect our twilio connection
                    self.assertTrue(self.org.is_connected_to_twilio())
                    self.client.post(twilio_account_url, dict(disconnect="true", follow=True))

                    self.org.refresh_from_db()
                    self.assertFalse(self.org.is_connected_to_twilio())

                    response = self.client.post(
                        f'{reverse("orgs.org_twilio_connect")}?claim_type=twilio', post_data, follow=True
                    )
                    self.assertEqual(response.request["PATH_INFO"], reverse("channels.types.twilio.claim"))

                    response = self.client.post(
                        f'{reverse("orgs.org_twilio_connect")}?claim_type=twilio_messaging_service',
                        post_data,
                        follow=True,
                    )
                    self.assertEqual(
                        response.request["PATH_INFO"], reverse("channels.types.twilio_messaging_service.claim")
                    )

                    response = self.client.post(
                        f'{reverse("orgs.org_twilio_connect")}?claim_type=twilio_whatsapp', post_data, follow=True
                    )
                    self.assertEqual(response.request["PATH_INFO"], reverse("channels.types.twilio_whatsapp.claim"))

                    response = self.client.post(
                        f'{reverse("orgs.org_twilio_connect")}?claim_type=unknown', post_data, follow=True
                    )
                    self.assertEqual(response.request["PATH_INFO"], reverse("channels.channel_claim"))

    def test_has_airtime_transfers(self):
        AirtimeTransfer.objects.filter(org=self.org).delete()
        self.assertFalse(self.org.has_airtime_transfers())
        contact = self.create_contact("Bob", phone="+250788123123")

        AirtimeTransfer.objects.create(
            org=self.org,
            contact=contact,
            status=AirtimeTransfer.STATUS_SUCCESS,
            recipient="+250788123123",
            desired_amount=Decimal("100"),
            actual_amount=Decimal("0"),
        )

        self.assertTrue(self.org.has_airtime_transfers())

    def test_prometheus(self):
        # visit as viewer, no prometheus section
        self.login(self.user)
        org_home_url = reverse("orgs.org_home")
        response = self.client.get(org_home_url)

        self.assertNotContains(response, "Prometheus")

        # admin can see it though
        self.login(self.admin)

        response = self.client.get(org_home_url)
        self.assertContains(response, "Prometheus")
        self.assertContains(response, "Enable Prometheus")

        # enable it
        prometheus_url = reverse("orgs.org_prometheus")
        response = self.client.post(prometheus_url, {}, follow=True)
        self.assertContains(response, "Disable Prometheus")

        # make sure our API token exists
        prometheus_group = Group.objects.get(name="Prometheus")
        self.assertTrue(APIToken.objects.filter(org=self.org, role=prometheus_group, is_active=True))

        # other admin sees it enabled too
        self.other_admin = self.create_user("Other Administrator")
        self.org.administrators.add(self.other_admin)
        self.login(self.other_admin)

        response = self.client.get(org_home_url)
        self.assertContains(response, "Prometheus")
        self.assertContains(response, "Disable Prometheus")

        # now disable it
        response = self.client.post(prometheus_url, {}, follow=True)
        self.assertFalse(APIToken.objects.filter(org=self.org, role=prometheus_group, is_active=True))
        self.assertContains(response, "Enable Prometheus")

    def test_resthooks(self):
        home_url = reverse("orgs.org_home")
        resthook_url = reverse("orgs.org_resthooks")

        # no hitting this page without auth
        response = self.client.get(resthook_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # get our resthook management page
        response = self.client.get(resthook_url)

        # shouldn't have any resthooks listed yet
        self.assertFalse(response.context["current_resthooks"])

        response = self.client.get(home_url)
        self.assertContains(response, "You have <b>no flow events</b> configured.")

        # try to create one with name that's too long
        response = self.client.post(resthook_url, {"new_slug": "x" * 100})
        self.assertFormError(response, "form", "new_slug", "Ensure this value has at most 50 characters (it has 100).")

        # now try to create with valid name/slug
        response = self.client.post(resthook_url, {"new_slug": "mother-registration "})
        self.assertEqual(302, response.status_code)

        # should now have a resthook
        mother_reg = Resthook.objects.get()
        self.assertEqual(mother_reg.slug, "mother-registration")
        self.assertEqual(mother_reg.org, self.org)
        self.assertEqual(mother_reg.created_by, self.admin)

        # fetch our read page, should have have our resthook
        response = self.client.get(resthook_url)
        self.assertEqual(
            [{"field": f"resthook_{mother_reg.id}", "resthook": mother_reg}],
            list(response.context["current_resthooks"]),
        )

        # and summarized on org home page
        response = self.client.get(home_url)
        self.assertContains(response, "You have <b>1 flow event</b> configured.")

        # let's try to create a repeat, should fail due to duplicate slug
        response = self.client.post(resthook_url, {"new_slug": "Mother-Registration"})
        self.assertFormError(response, "form", "new_slug", "This event name has already been used.")

        # add a subscriber
        subscriber = mother_reg.add_subscriber("http://foo", self.admin)

        # finally, let's remove that resthook
        self.client.post(resthook_url, {"resthook_%d" % mother_reg.id: "checked"})

        mother_reg.refresh_from_db()
        self.assertFalse(mother_reg.is_active)

        subscriber.refresh_from_db()
        self.assertFalse(subscriber.is_active)

        # no more resthooks!
        response = self.client.get(resthook_url)
        self.assertEqual([], list(response.context["current_resthooks"]))

    @override_settings(HOSTNAME="rapidpro.io", SEND_EMAILS=True)
    def test_smtp_server(self):
        self.login(self.admin)

        home_url = reverse("orgs.org_home")
        config_url = reverse("orgs.org_smtp_server")

        # orgs without SMTP settings see default from address
        response = self.client.get(home_url)
        self.assertContains(response, "Emails sent from flows will be sent from <b>no-reply@temba.io</b>.")
        self.assertEqual("no-reply@temba.io", response.context["from_email_default"])
        self.assertEqual(None, response.context["from_email_custom"])

        self.assertFalse(self.org.has_smtp_config())

        response = self.client.post(config_url, dict(disconnect="false"), follow=True)
        self.assertEqual(
            '[{"message": "You must enter a from email", "code": ""}]',
            response.context["form"].errors["__all__"].as_json(),
        )
        self.assertEqual(len(mail.outbox), 0)

        response = self.client.post(config_url, {"from_email": "foobar.com", "disconnect": "false"}, follow=True)
        self.assertEqual(
            '[{"message": "Please enter a valid email address", "code": ""}]',
            response.context["form"].errors["__all__"].as_json(),
        )
        self.assertEqual(len(mail.outbox), 0)

        response = self.client.post(config_url, {"from_email": "foo@bar.com", "disconnect": "false"}, follow=True)
        self.assertEqual(
            '[{"message": "You must enter the SMTP host", "code": ""}]',
            response.context["form"].errors["__all__"].as_json(),
        )
        self.assertEqual(len(mail.outbox), 0)

        response = self.client.post(
            config_url,
            {"from_email": "foo@bar.com", "smtp_host": "smtp.example.com", "disconnect": "false"},
            follow=True,
        )
        self.assertEqual(
            '[{"message": "You must enter the SMTP username", "code": ""}]',
            response.context["form"].errors["__all__"].as_json(),
        )
        self.assertEqual(len(mail.outbox), 0)

        response = self.client.post(
            config_url,
            {
                "from_email": "foo@bar.com",
                "smtp_host": "smtp.example.com",
                "smtp_username": "support@example.com",
                "disconnect": "false",
            },
            follow=True,
        )
        self.assertEqual(
            '[{"message": "You must enter the SMTP password", "code": ""}]',
            response.context["form"].errors["__all__"].as_json(),
        )
        self.assertEqual(len(mail.outbox), 0)

        response = self.client.post(
            config_url,
            {
                "from_email": "foo@bar.com",
                "smtp_host": "smtp.example.com",
                "smtp_username": "support@example.com",
                "smtp_password": "secret",
                "disconnect": "false",
            },
            follow=True,
        )
        self.assertEqual(
            '[{"message": "You must enter the SMTP port", "code": ""}]',
            response.context["form"].errors["__all__"].as_json(),
        )
        self.assertEqual(len(mail.outbox), 0)

        with patch("temba.utils.email.send_custom_smtp_email") as mock_send_smtp_email:
            mock_send_smtp_email.side_effect = smtplib.SMTPException("SMTP Error")
            response = self.client.post(
                config_url,
                {
                    "from_email": "foo@bar.com",
                    "smtp_host": "smtp.example.com",
                    "smtp_username": "support@example.com",
                    "smtp_password": "secret",
                    "smtp_port": "465",
                    "disconnect": "false",
                },
                follow=True,
            )
            self.assertEqual(
                '[{"message": "Failed to send email with STMP server configuration with error \'SMTP Error\'", "code": ""}]',
                response.context["form"].errors["__all__"].as_json(),
            )
            self.assertEqual(len(mail.outbox), 0)

            mock_send_smtp_email.side_effect = Exception("Unexpected Error")
            response = self.client.post(
                config_url,
                {
                    "from_email": "foo@bar.com",
                    "smtp_host": "smtp.example.com",
                    "smtp_username": "support@example.com",
                    "smtp_password": "secret",
                    "smtp_port": "465",
                    "disconnect": "false",
                },
                follow=True,
            )
            self.assertEqual(
                '[{"message": "Failed to send email with STMP server configuration", "code": ""}]',
                response.context["form"].errors["__all__"].as_json(),
            )
            self.assertEqual(len(mail.outbox), 0)

        response = self.client.post(
            config_url,
            {
                "from_email": "foo@bar.com",
                "smtp_host": "smtp.example.com",
                "smtp_username": "support@example.com",
                "smtp_password": "secret",
                "smtp_port": "465",
                "disconnect": "false",
            },
            follow=True,
        )
        self.assertEqual(len(mail.outbox), 1)

        self.org.refresh_from_db()
        self.assertTrue(self.org.has_smtp_config())

        self.assertEqual(
            self.org.config["smtp_server"],
            "smtp://support%40example.com:secret@smtp.example.com:465/?from=foo%40bar.com&tls=true",
        )

        response = self.client.get(config_url)
        self.assertEqual("no-reply@temba.io", response.context["from_email_default"])
        self.assertEqual("foo@bar.com", response.context["from_email_custom"])

        self.client.post(
            config_url,
            {
                "from_email": "support@example.com",
                "smtp_host": "smtp.example.com",
                "smtp_username": "support@example.com",
                "smtp_password": "secret",
                "smtp_port": "465",
                "name": "DO NOT CHANGE ME",
                "disconnect": "false",
            },
            follow=True,
        )

        # name shouldn't change
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Temba")
        self.assertTrue(self.org.has_smtp_config())

        self.client.post(
            config_url,
            {
                "from_email": "support@example.com",
                "smtp_host": "smtp.example.com",
                "smtp_username": "support@example.com",
                "smtp_password": "",
                "smtp_port": "465",
                "disconnect": "false",
            },
            follow=True,
        )

        # password shouldn't change
        self.org.refresh_from_db()
        self.assertTrue(self.org.has_smtp_config())
        self.assertEqual(
            self.org.config["smtp_server"],
            "smtp://support%40example.com:secret@smtp.example.com:465/?from=support%40example.com&tls=true",
        )

        response = self.client.post(
            config_url,
            {
                "from_email": "support@example.com",
                "smtp_host": "smtp.example.com",
                "smtp_username": "help@example.com",
                "smtp_password": "",
                "smtp_port": "465",
                "disconnect": "false",
            },
            follow=True,
        )

        # should have error for blank password
        self.assertEqual(
            '[{"message": "You must enter the SMTP password", "code": ""}]',
            response.context["form"].errors["__all__"].as_json(),
        )

        self.client.post(config_url, dict(disconnect="true"), follow=True)

        self.org.refresh_from_db()
        self.assertFalse(self.org.has_smtp_config())

        response = self.client.post(
            config_url,
            {
                "from_email": " support@example.com",
                "smtp_host": " smtp.example.com",
                "smtp_username": "support@example.com",
                "smtp_password": "secret ",
                "smtp_port": "465 ",
                "disconnect": "false",
            },
            follow=True,
        )

        self.org.refresh_from_db()
        self.assertTrue(self.org.has_smtp_config())

        self.assertEqual(
            self.org.config["smtp_server"],
            "smtp://support%40example.com:secret@smtp.example.com:465/?from=support%40example.com&tls=true",
        )

        response = self.client.post(
            config_url,
            {
                "from_email": "support@example.com",
                "smtp_host": "smtp.example.com",
                "smtp_username": "support@example.com",
                "smtp_password": "secre/t",
                "smtp_port": 465,
                "disconnect": "false",
            },
            follow=True,
        )

        self.org.refresh_from_db()
        self.assertTrue(self.org.has_smtp_config())

        self.assertEqual(
            self.org.config["smtp_server"],
            "smtp://support%40example.com:secre%2Ft@smtp.example.com:465/?from=support%40example.com&tls=true",
        )

        response = self.client.get(config_url)
        self.assertDictEqual(
            response.context["view"].derive_initial(),
            {
                "from_email": "support@example.com",
                "smtp_host": "smtp.example.com",
                "smtp_username": "support@example.com",
                "smtp_password": "secre/t",
                "smtp_port": 465,
                "disconnect": "false",
            },
        )

    @patch("temba.channels.types.vonage.client.VonageClient.check_credentials")
    def test_connect_vonage(self, mock_check_credentials):
        self.login(self.admin)

        connect_url = reverse("orgs.org_vonage_connect")
        account_url = reverse("orgs.org_vonage_account")

        # simulate invalid credentials on both pages
        mock_check_credentials.return_value = False

        response = self.client.post(connect_url, {"api_key": "key", "api_secret": "secret"})
        self.assertContains(response, "Your API key and secret seem invalid.")
        self.assertFalse(self.org.is_connected_to_vonage())

        response = self.client.post(account_url, {"api_key": "key", "api_secret": "secret"})
        self.assertContains(response, "Your API key and secret seem invalid.")

        # ok, now with a success
        mock_check_credentials.return_value = True

        response = self.client.post(connect_url, {"api_key": "key", "api_secret": "secret"})
        self.assertEqual(response.status_code, 302)

        response = self.client.get(account_url)
        self.assertEqual("key", response.context["api_key"])

        self.org.refresh_from_db()
        self.assertEqual("key", self.org.config[Org.CONFIG_VONAGE_KEY])
        self.assertEqual("secret", self.org.config[Org.CONFIG_VONAGE_SECRET])

        # post without API token, should get validation error
        response = self.client.post(account_url, {"disconnect": "false"})
        self.assertFormError(response, "form", "__all__", "You must enter your account API Key")

        # vonage config should remain the same
        self.org.refresh_from_db()
        self.assertEqual("key", self.org.config[Org.CONFIG_VONAGE_KEY])
        self.assertEqual("secret", self.org.config[Org.CONFIG_VONAGE_SECRET])

        # now try with all required fields, and a bonus field we shouldn't change
        self.client.post(
            account_url,
            {"api_key": "other_key", "api_secret": "secret-too", "disconnect": "false", "name": "DO NOT CHANGE ME"},
        )
        # name shouldn't change
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Temba")

        # should change vonage config
        self.client.post(account_url, {"api_key": "other_key", "api_secret": "secret-too", "disconnect": "false"})

        self.org.refresh_from_db()
        self.assertEqual("other_key", self.org.config[Org.CONFIG_VONAGE_KEY])
        self.assertEqual("secret-too", self.org.config[Org.CONFIG_VONAGE_SECRET])

        self.assertTrue(self.org.is_connected_to_vonage())
        self.client.post(account_url, dict(disconnect="true"), follow=True)

        self.org.refresh_from_db()
        self.assertFalse(self.org.is_connected_to_vonage())

        # and disconnect
        self.org.remove_vonage_account(self.admin)
        self.assertFalse(self.org.is_connected_to_vonage())
        self.assertNotIn("NEXMO_KEY", self.org.config)
        self.assertNotIn("NEXMO_SECRET", self.org.config)

    def test_connect_plivo(self):
        self.login(self.admin)

        # connect plivo
        connect_url = reverse("orgs.org_plivo_connect")

        # simulate invalid credentials
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(
                401, "Could not verify your access level for that URL." "\nYou have to login with proper credentials"
            )
            response = self.client.post(connect_url, dict(auth_id="auth-id", auth_token="auth-token"))
            self.assertContains(
                response, "Your Plivo auth ID and auth token seem invalid. Please check them again and retry."
            )
            self.assertFalse(Channel.CONFIG_PLIVO_AUTH_ID in self.client.session)
            self.assertFalse(Channel.CONFIG_PLIVO_AUTH_TOKEN in self.client.session)

        # ok, now with a success
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, json.dumps(dict()))
            response = self.client.post(connect_url, dict(auth_id="auth-id", auth_token="auth-token"))

            # plivo should be added to the session
            self.assertEqual(self.client.session[Channel.CONFIG_PLIVO_AUTH_ID], "auth-id")
            self.assertEqual(self.client.session[Channel.CONFIG_PLIVO_AUTH_TOKEN], "auth-token")

            self.assertRedirect(response, reverse("channels.types.plivo.claim"))

    def test_tiers(self):
        # default is no tiers, everything is allowed, go crazy!
        self.assertTrue(self.org.is_multi_user)
        self.assertTrue(self.org.is_multi_org)

        del settings.BRANDING[settings.DEFAULT_BRAND]["tiers"]
        self.org.reset_capabilities()
        self.assertTrue(self.org.is_multi_user)
        self.assertTrue(self.org.is_multi_org)

        # not enough credits with tiers enabled
        settings.BRANDING[settings.DEFAULT_BRAND]["tiers"] = dict(
            import_flows=1, multi_user=100_000, multi_org=1_000_000
        )
        self.org.reset_capabilities()
        self.assertIsNone(self.org.create_sub_org("Sub Org A"))
        self.assertFalse(self.org.is_multi_user)
        self.assertFalse(self.org.is_multi_org)

        # not enough credits, but tiers disabled
        settings.BRANDING[settings.DEFAULT_BRAND]["tiers"] = dict(import_flows=0, multi_user=0, multi_org=0)
        self.org.reset_capabilities()
        self.assertIsNotNone(self.org.create_sub_org("Sub Org A"))
        self.assertTrue(self.org.is_multi_user)
        self.assertTrue(self.org.is_multi_org)

        # tiers enabled, but enough credits
        settings.BRANDING[settings.DEFAULT_BRAND]["tiers"] = dict(
            import_flows=1, multi_user=100_000, multi_org=1_000_000
        )
        TopUp.create(self.admin, price=100, credits=1_000_000)
        self.org.clear_credit_cache()
        self.assertIsNotNone(self.org.create_sub_org("Sub Org B"))
        self.assertTrue(self.org.is_multi_user)
        self.assertTrue(self.org.is_multi_org)

        with override_settings(DEFAULT_PLAN="other"):
            settings.BRANDING[settings.DEFAULT_BRAND]["default_plan"] = "other"
            self.org.plan = settings.TOPUP_PLAN
            self.org.save()
            self.org.reset_capabilities()
            sub_org_c = self.org.create_sub_org("Sub Org C")
            self.assertIsNotNone(sub_org_c)
            self.assertEqual(sub_org_c.plan, settings.TOPUP_PLAN)

    def test_org_get_limit(self):
        self.assertEqual(self.org.get_limit(Org.LIMIT_FIELDS), 250)
        self.assertEqual(self.org.get_limit(Org.LIMIT_GROUPS), 250)
        self.assertEqual(self.org.get_limit(Org.LIMIT_GLOBALS), 250)

        self.org.limits = dict(fields=500, groups=500)
        self.org.save()

        self.assertEqual(self.org.get_limit(Org.LIMIT_FIELDS), 500)
        self.assertEqual(self.org.get_limit(Org.LIMIT_GROUPS), 500)
        self.assertEqual(self.org.get_limit(Org.LIMIT_GLOBALS), 250)

    def test_sub_orgs_management(self):
        settings.BRANDING[settings.DEFAULT_BRAND]["tiers"] = dict(multi_org=1_000_000)
        self.org.reset_capabilities()

        sub_org = self.org.create_sub_org("Sub Org")

        # we won't create sub orgs if the org isn't the proper level
        self.assertIsNone(sub_org)

        # lower the tier and try again
        settings.BRANDING[settings.DEFAULT_BRAND]["tiers"] = dict(multi_org=0)
        self.org.reset_capabilities()
        sub_org = self.org.create_sub_org("Sub Org")

        # suborg has been created
        self.assertIsNotNone(sub_org)

        # suborgs can create suborgs
        self.assertIsNotNone(sub_org.create_sub_org("Grandchild Org"))

        # we should be linked to our parent with the same brand
        self.assertEqual(self.org, sub_org.parent)
        self.assertEqual(self.org.brand, sub_org.brand)

        # default values should be the same as parent
        self.assertEqual(self.org.timezone, sub_org.timezone)
        self.assertEqual(self.org.created_by, sub_org.created_by)

        # our sub account should have zero credits
        self.assertEqual(0, sub_org.get_credits_remaining())

        self.login(self.admin)
        response = self.client.get(reverse("orgs.org_edit"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(len(response.context["sub_orgs"]), 1)

        # sub_org is deleted
        sub_org.release(self.superuser)

        response = self.client.get(reverse("orgs.org_edit"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(len(response.context["sub_orgs"]), 0)

    def test_sub_orgs(self):
        # lets start with two topups
        oldest_topup = TopUp.objects.filter(org=self.org).first()

        expires = timezone.now() + timedelta(days=400)
        newer_topup = TopUp.create(self.admin, price=0, credits=1000, org=self.org, expires_on=expires)

        # lower the tier and try again
        settings.BRANDING[settings.DEFAULT_BRAND]["tiers"] = dict(multi_org=0)
        sub_org = self.org.create_sub_org("Sub Org")

        # send a message as sub_org
        self.create_channel(
            "A",
            "Test Channel",
            "+250785551212",
            secret="12355",
            config={Channel.CONFIG_FCM_ID: "145"},
            country="RW",
            org=sub_org,
        )
        contact = self.create_contact("Joe", phone="+250788383444", org=sub_org)
        msg = self.create_outgoing_msg(contact, "How is it going?")

        # there is no topup on suborg, and this msg won't be credited
        self.assertFalse(msg.topup)

        # now allocate some credits to our sub org
        self.assertTrue(self.org.allocate_credits(self.admin, sub_org, 700))

        msg.refresh_from_db()
        # allocating credits will execute apply_topups_task and assign a topup
        self.assertTrue(msg.topup)

        self.assertEqual(699, sub_org.get_credits_remaining())
        self.assertEqual(1300, self.org.get_credits_remaining())

        # we should have a debit to track this transaction
        debits = Debit.objects.filter(topup__org=self.org)
        self.assertEqual(1, len(debits))

        debit = debits.first()
        self.assertEqual(700, debit.amount)
        self.assertEqual(Debit.TYPE_ALLOCATION, debit.debit_type)
        # newest topup has been used first
        self.assertEqual(newer_topup.expires_on, debit.beneficiary.expires_on)
        self.assertEqual(debit.amount, 700)

        # try allocating more than we have
        self.assertFalse(self.org.allocate_credits(self.admin, sub_org, 1301))

        self.assertEqual(699, sub_org.get_credits_remaining())
        self.assertEqual(1300, self.org.get_credits_remaining())
        self.assertEqual(700, self.org._calculate_credits_used()[0])

        # now allocate across our remaining topups
        self.assertTrue(self.org.allocate_credits(self.admin, sub_org, 1200))
        self.assertEqual(1899, sub_org.get_credits_remaining())
        self.assertEqual(1900, self.org.get_credits_used())
        self.assertEqual(100, self.org.get_credits_remaining())

        # now clear our cache, we ought to have proper amount still
        self.org.clear_credit_cache()
        sub_org.clear_credit_cache()

        self.assertEqual(1899, sub_org.get_credits_remaining())
        self.assertEqual(100, self.org.get_credits_remaining())

        # this creates two more debits, for a total of three
        debits = Debit.objects.filter(topup__org=self.org).order_by("id")
        self.assertEqual(3, len(debits))

        # verify that we used most recent topup first
        self.assertEqual(newer_topup.expires_on, debits[1].topup.expires_on)
        self.assertEqual(debits[1].amount, 300)
        # and debited missing amount from the next topup
        self.assertEqual(oldest_topup.expires_on, debits[2].topup.expires_on)
        self.assertEqual(debits[2].amount, 900)

        # allocate the exact number of credits remaining
        self.org.allocate_credits(self.admin, sub_org, 100)

        self.assertEqual(1999, sub_org.get_credits_remaining())
        self.assertEqual(0, self.org.get_credits_remaining())

    def test_sub_org_ui(self):
        self.login(self.admin)

        settings.BRANDING[settings.DEFAULT_BRAND]["tiers"] = dict(multi_org=1_000_000)
        self.org.reset_capabilities()

        # set our org on the session
        session = self.client.session
        session["org_id"] = self.org.id
        session.save()

        response = self.client.get(reverse("orgs.org_home"))
        self.assertNotContains(response, "Manage Workspaces")

        # attempting to manage orgs should redirect
        response = self.client.get(reverse("orgs.org_sub_orgs"))
        self.assertRedirect(response, reverse("orgs.org_home"))

        # creating a new sub org should also redirect
        response = self.client.get(reverse("orgs.org_create_sub_org"))
        self.assertRedirect(response, reverse("orgs.org_home"))

        # make sure posting is gated too
        new_org = dict(name="Sub Org", timezone=self.org.timezone, date_format=self.org.date_format)
        response = self.client.post(reverse("orgs.org_create_sub_org"), new_org)
        self.assertRedirect(response, reverse("orgs.org_home"))

        # same thing with trying to transfer credits
        response = self.client.get(reverse("orgs.org_transfer_credits"))
        self.assertRedirect(response, reverse("orgs.org_home"))

        # cant manage users either
        response = self.client.get(reverse("orgs.org_manage_accounts_sub_org"))
        self.assertRedirect(response, reverse("orgs.org_home"))

        # zero out our tier
        settings.BRANDING[settings.DEFAULT_BRAND]["tiers"] = dict(multi_org=0)
        self.org.reset_capabilities()
        self.assertTrue(self.org.is_multi_org)
        response = self.client.get(reverse("orgs.org_home"))
        self.assertContains(response, "Manage Workspaces")

        # now we can manage our orgs
        response = self.client.get(reverse("orgs.org_sub_orgs"))
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Workspaces")

        # and add topups
        self.assertContains(response, reverse("orgs.org_transfer_credits"))

        # but not if we don't use topups
        Org.objects.filter(id=self.org.id).update(uses_topups=False)
        response = self.client.get(reverse("orgs.org_sub_orgs"))
        self.assertNotContains(response, reverse("orgs.org_transfer_credits"))

        Org.objects.filter(id=self.org.id).update(uses_topups=True)

        # add a sub org
        response = self.client.post(reverse("orgs.org_create_sub_org"), new_org)
        self.assertRedirect(response, reverse("orgs.org_sub_orgs"))
        sub_org = Org.objects.filter(name="Sub Org").first()
        self.assertIsNotNone(sub_org)
        self.assertIn(self.admin, sub_org.administrators.all())

        # create a second org to test sorting
        new_org = dict(name="A Second Org", timezone=self.org.timezone, date_format=self.org.date_format)
        response = self.client.post(reverse("orgs.org_create_sub_org"), new_org)
        self.assertEqual(302, response.status_code)

        # load the transfer credit page
        response = self.client.get(reverse("orgs.org_transfer_credits"))

        # check that things are ordered correctly
        orgs = list(response.context["form"]["from_org"].field._queryset)
        self.assertEqual("A Second Org", orgs[1].name)
        self.assertEqual("Sub Org", orgs[2].name)
        self.assertEqual(200, response.status_code)

        # try to transfer more than we have
        post_data = dict(from_org=self.org.id, to_org=sub_org.id, amount=1500)
        response = self.client.post(reverse("orgs.org_transfer_credits"), post_data)
        self.assertContains(response, "Pick a different workspace to transfer from")

        # now transfer some credits
        post_data = dict(from_org=self.org.id, to_org=sub_org.id, amount=600)
        response = self.client.post(reverse("orgs.org_transfer_credits"), post_data)

        self.assertEqual(400, self.org.get_credits_remaining())
        self.assertEqual(600, sub_org.get_credits_remaining())

        # we can reach the manage accounts page too now
        response = self.client.get("%s?org=%d" % (reverse("orgs.org_manage_accounts_sub_org"), sub_org.id))
        self.assertEqual(200, response.status_code)

        # edit our sub org's details
        response = self.client.post(
            f"{reverse('orgs.org_edit_sub_org')}?org={sub_org.id}",
            {"name": "New Sub Org Name", "timezone": "Africa/Nairobi", "date_format": "Y", "language": "es"},
        )

        sub_org.refresh_from_db()
        self.assertEqual("New Sub Org Name", sub_org.name)
        self.assertEqual("Africa/Nairobi", str(sub_org.timezone))
        self.assertEqual("Y", sub_org.date_format)
        self.assertEqual("es", sub_org.language)

        # now we should see new topups on our sub org
        session["org_id"] = sub_org.id
        session.save()

        response = self.client.get(reverse("orgs.topup_list"))
        self.assertContains(response, "600 Credits")

    def test_account_value(self):

        # base value
        self.assertEqual(self.org.account_value(), 0.0)

        # add a topup
        TopUp.objects.create(
            org=self.org,
            price=123,
            credits=1001,
            expires_on=timezone.now() + timedelta(days=30),
            created_by=self.admin,
            modified_by=self.admin,
        )
        self.assertAlmostEqual(self.org.account_value(), 1.23)

    @patch("temba.msgs.tasks.export_messages_task.delay")
    @patch("temba.flows.tasks.export_flow_results_task.delay")
    @patch("temba.contacts.tasks.export_contacts_task.delay")
    def test_resume_failed_task(
        self, mock_export_contacts_task, mock_export_flow_results_task, mock_export_messages_task
    ):
        mock_export_contacts_task.return_value = None
        mock_export_flow_results_task.return_value = None
        mock_export_messages_task.return_value = None

        ExportMessagesTask.objects.create(
            org=self.org, created_by=self.admin, modified_by=self.admin, status=ExportMessagesTask.STATUS_FAILED
        )
        ExportMessagesTask.objects.create(
            org=self.org, created_by=self.admin, modified_by=self.admin, status=ExportMessagesTask.STATUS_COMPLETE
        )
        ExportMessagesTask.objects.create(org=self.org, created_by=self.admin, modified_by=self.admin)

        ExportFlowResultsTask.objects.create(
            org=self.org, created_by=self.admin, modified_by=self.admin, status=ExportFlowResultsTask.STATUS_FAILED
        )
        ExportFlowResultsTask.objects.create(
            org=self.org, created_by=self.admin, modified_by=self.admin, status=ExportFlowResultsTask.STATUS_COMPLETE
        )
        ExportFlowResultsTask.objects.create(org=self.org, created_by=self.admin, modified_by=self.admin)

        ExportContactsTask.objects.create(
            org=self.org, created_by=self.admin, modified_by=self.admin, status=ExportContactsTask.STATUS_FAILED
        )
        ExportContactsTask.objects.create(
            org=self.org, created_by=self.admin, modified_by=self.admin, status=ExportContactsTask.STATUS_COMPLETE
        )
        ExportContactsTask.objects.create(org=self.org, created_by=self.admin, modified_by=self.admin)

        two_hours_ago = timezone.now() - timedelta(hours=2)

        ExportMessagesTask.objects.all().update(modified_on=two_hours_ago)
        ExportFlowResultsTask.objects.all().update(modified_on=two_hours_ago)
        ExportContactsTask.objects.all().update(modified_on=two_hours_ago)

        resume_failed_tasks()

        mock_export_contacts_task.assert_called_once()
        mock_export_flow_results_task.assert_called_once()
        mock_export_messages_task.assert_called_once()


class AnonOrgTest(TembaTest):
    """
    Tests the case where our organization is marked as anonymous, that is the phone numbers are masked
    for users.
    """

    def setUp(self):
        super().setUp()

        self.org.is_anon = True
        self.org.save()

    def test_contacts(self):
        # are there real phone numbers on the contact list page?
        contact = self.create_contact(None, phone="+250788123123")
        self.login(self.admin)

        masked = "%010d" % contact.pk

        response = self.client.get(reverse("contacts.contact_list"))

        # phone not in the list
        self.assertNotContains(response, "788 123 123")

        # but the id is
        self.assertContains(response, masked)
        self.assertContains(response, ContactURN.ANON_MASK_HTML)

        # create an outgoing message, check number doesn't appear in outbox
        msg1 = self.create_outgoing_msg(contact, "hello", status="Q")

        response = self.client.get(reverse("msgs.msg_outbox"))

        self.assertEqual(set(response.context["object_list"]), {msg1})
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, masked)

        # create an incoming message, check number doesn't appear in inbox
        msg2 = self.create_incoming_msg(contact, "ok")

        response = self.client.get(reverse("msgs.msg_inbox"))

        self.assertEqual(set(response.context["object_list"]), {msg2})
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, masked)

        # create an incoming flow message, check number doesn't appear in inbox
        msg3 = self.create_incoming_msg(contact, "ok", msg_type="F")

        response = self.client.get(reverse("msgs.msg_flow"))

        self.assertEqual(set(response.context["object_list"]), {msg3})
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, masked)

        # check contact detail page
        response = self.client.get(reverse("contacts.contact_read", args=[contact.uuid]))
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, masked)


class OrgCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_spa(self):
        self.make_beta(self.admin)
        self.login(self.admin)
        deep_link = reverse("spa.level_2", args=["tickets", "all", "open"])
        response = self.client.get(deep_link)
        self.assertEqual(200, response.status_code)

    def test_menu(self):
        menu_url = reverse("orgs.org_menu")
        response = self.assertListFetch(menu_url, allow_viewers=True, allow_editors=True, allow_agents=True)
        menu = response.json()["results"]
        self.assertEqual(
            [
                {"endpoint": "/msg/menu/", "icon": "message-square", "id": "messages", "name": "Messages"},
                {"id": "contacts", "name": "Contacts", "icon": "contact", "endpoint": "/contact/menu/"},
                {"id": "tickets", "name": "Tickets", "icon": "agent", "href": "/ticket/", "endpoint": "/ticket/menu/"},
                {"endpoint": "/channels/channel/menu/", "icon": "zap", "id": "channels", "name": "Channels"},
                {
                    "id": "support",
                    "name": "Support",
                    "icon": "help-circle",
                    "bottom": True,
                    "trigger": "showSupportWidget",
                },
            ],
            menu,
        )

        # agents should only see tickets and support
        self.login(self.agent)
        response = self.client.get(menu_url)
        menu = response.json()["results"]
        self.assertEqual(
            [
                {"id": "tickets", "name": "Tickets", "icon": "agent", "href": "/ticket/", "endpoint": "/ticket/menu/"},
                {
                    "id": "support",
                    "name": "Support",
                    "icon": "help-circle",
                    "bottom": True,
                    "trigger": "showSupportWidget",
                },
            ],
            menu,
        )

    def test_org_grant(self):
        grant_url = reverse("orgs.org_grant")
        response = self.client.get(grant_url)
        self.assertRedirect(response, "/users/login/")

        self.user = self.create_user(username="tito")

        self.login(self.user)
        response = self.client.get(grant_url)
        self.assertRedirect(response, "/users/login/")

        granters = Group.objects.get(name="Granters")
        self.user.groups.add(granters)

        response = self.client.get(grant_url)
        self.assertEqual(200, response.status_code)

        # fill out the form
        post_data = dict(
            email="john@carmack.com",
            first_name="John",
            last_name="Carmack",
            name="Oculus",
            timezone="Africa/Kigali",
            credits="100000",
            password="dukenukem",
        )
        response = self.client.post(grant_url, post_data, follow=True)

        self.assertContains(response, "created")

        org = Org.objects.get(name="Oculus")
        self.assertEqual(100_000, org.get_credits_remaining())
        self.assertEqual(org.date_format, Org.DATE_FORMAT_DAY_FIRST)

        # check user exists and is admin
        User.objects.get(username="john@carmack.com")
        self.assertTrue(org.administrators.filter(username="john@carmack.com"))
        self.assertTrue(org.administrators.filter(username="tito"))

        # try a new org with a user that already exists instead
        del post_data["password"]
        post_data["name"] = "id Software"

        response = self.client.post(grant_url, post_data, follow=True)

        self.assertContains(response, "created")

        org = Org.objects.get(name="id Software")
        self.assertEqual(100_000, org.get_credits_remaining())
        self.assertEqual(org.date_format, Org.DATE_FORMAT_DAY_FIRST)

        self.assertTrue(org.administrators.filter(username="john@carmack.com"))
        self.assertTrue(org.administrators.filter(username="tito"))

        # try a new org with US timezone
        post_data["name"] = "Bulls"
        post_data["timezone"] = "America/Chicago"
        response = self.client.post(grant_url, post_data, follow=True)

        self.assertContains(response, "created")

        org = Org.objects.get(name="Bulls")
        self.assertEqual(100_000, org.get_credits_remaining())
        self.assertEqual(Org.DATE_FORMAT_MONTH_FIRST, org.date_format)
        self.assertEqual("en-us", org.language)
        self.assertEqual(["eng"], org.flow_languages)

    def test_org_grant_invalid_form(self):
        grant_url = reverse("orgs.org_grant")

        granters = Group.objects.get(name="Granters")
        self.admin.groups.add(granters)

        self.login(self.admin)

        post_data = dict(
            email="",
            first_name="John",
            last_name="Carmack",
            name="Oculus",
            timezone="Africa/Kigali",
            credits="100000",
            password="dukenukem",
        )
        response = self.client.post(grant_url, post_data)
        self.assertFormError(response, "form", "email", "This field is required.")

        post_data = dict(
            email="this-is-not-a-valid-email",
            first_name="John",
            last_name="Carmack",
            name="Oculus",
            timezone="Africa/Kigali",
            credits="100000",
            password="dukenukem",
        )
        response = self.client.post(grant_url, post_data)
        self.assertFormError(response, "form", "email", "Enter a valid email address.")

        response = self.client.post(
            grant_url,
            {
                "email": f"john@{'x' * 150}.com",
                "first_name": f"John@{'n' * 150}.com",
                "last_name": f"Carmack@{'k' * 150}.com",
                "name": f"Oculus{'s' * 130}",
                "timezone": "Africa/Kigali",
                "credits": "100000",
                "password": "dukenukem",
            },
        )
        self.assertFormError(
            response, "form", "first_name", "Ensure this value has at most 150 characters (it has 159)."
        )
        self.assertFormError(
            response, "form", "last_name", "Ensure this value has at most 150 characters (it has 162)."
        )
        self.assertFormError(response, "form", "name", "Ensure this value has at most 128 characters (it has 136).")
        self.assertFormError(response, "form", "email", "Ensure this value has at most 150 characters (it has 159).")
        self.assertFormError(response, "form", "email", "Enter a valid email address.")

    def test_org_grant_form_clean(self):
        grant_url = reverse("orgs.org_grant")

        granters = Group.objects.get(name="Granters")
        self.admin.groups.add(granters)
        self.admin.username = "Administrator@nyaruka.com"
        self.admin.set_password("Administrator@nyaruka.com")
        self.admin.save()

        self.login(self.admin)

        # user with email Administrator@nyaruka.com already exists and we set a password
        response = self.client.post(
            grant_url,
            {
                "email": "Administrator@nyaruka.com",
                "first_name": "John",
                "last_name": "Carmack",
                "name": "Oculus",
                "timezone": "Africa/Kigali",
                "credits": "100000",
                "password": "password",
            },
        )
        self.assertFormError(response, "form", None, "Login already exists, please do not include password.")

        # try to create a new user with empty password
        response = self.client.post(
            grant_url,
            {
                "email": "a_new_user@nyaruka.com",
                "first_name": "John",
                "last_name": "Carmack",
                "name": "Oculus",
                "timezone": "Africa/Kigali",
                "credits": "100000",
                "password": "",
            },
        )
        self.assertFormError(response, "form", None, "Password required for new login.")

        # try to create a new user with invalid password
        response = self.client.post(
            grant_url,
            {
                "email": "a_new_user@nyaruka.com",
                "first_name": "John",
                "last_name": "Carmack",
                "name": "Oculus",
                "timezone": "Africa/Kigali",
                "credits": "100000",
                "password": "pass",
            },
        )
        self.assertFormError(
            response, "form", None, "This password is too short. It must contain at least 8 characters."
        )

    @patch("temba.orgs.views.OrgCRUDL.Signup.pre_process")
    def test_new_signup_with_user_logged_in(self, mock_pre_process):
        mock_pre_process.return_value = None
        signup_url = reverse("orgs.org_signup")
        self.user = self.create_user(username="tito")

        self.login(self.user)

        response = self.client.get(signup_url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            signup_url,
            {
                "first_name": "Kellan",
                "last_name": "Alexander",
                "email": "kellan@example.com",
                "password": "HeyThere123",
                "name": "AlexCom",
                "timezone": "Africa/Kigali",
            },
        )
        self.assertEqual(response.status_code, 302)

        # should have a new user
        user = User.objects.get(username="kellan@example.com")
        self.assertEqual(user.first_name, "Kellan")
        self.assertEqual(user.last_name, "Alexander")
        self.assertEqual(user.email, "kellan@example.com")
        self.assertTrue(user.check_password("HeyThere123"))
        self.assertTrue(user.api_token)  # should be able to generate an API token

        # should have a new org
        org = Org.objects.get(name="AlexCom")
        self.assertEqual(org.timezone, pytz.timezone("Africa/Kigali"))

        # of which our user is an administrator
        self.assertTrue(org.get_admins().filter(pk=user.pk))

        # not the logged in user at the signup time
        self.assertFalse(org.get_admins().filter(pk=self.user.pk))

    @override_settings(DEFAULT_BRAND="no-topups.org")
    def test_no_topup_signup(self):
        signup_url = reverse("orgs.org_signup")
        response = self.client.post(
            signup_url,
            {
                "first_name": "Eugene",
                "last_name": "Rwagasore",
                "email": "test@foo.org",
                "password": "HeyThere123",
                "name": "No Topups",
                "timezone": "Africa/Kigali",
            },
        )
        self.assertEqual(response.status_code, 302)

        org = Org.objects.get(name="No Topups")
        self.assertEqual(org.timezone, pytz.timezone("Africa/Kigali"))
        self.assertFalse(org.uses_topups)

    @override_settings(
        AUTH_PASSWORD_VALIDATORS=[
            {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
            {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
            {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
        ]
    )
    def test_org_signup(self):
        signup_url = reverse("orgs.org_signup")

        response = self.client.get(signup_url + "?%s" % urlencode({"email": "address@example.com"}))
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].fields)
        self.assertEqual(response.context["view"].derive_initial()["email"], "address@example.com")

        response = self.client.get(signup_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.context["form"].fields)

        # submit with missing fields
        response = self.client.post(signup_url, {})
        self.assertFormError(response, "form", "name", "This field is required.")
        self.assertFormError(response, "form", "first_name", "This field is required.")
        self.assertFormError(response, "form", "last_name", "This field is required.")
        self.assertFormError(response, "form", "email", "This field is required.")
        self.assertFormError(response, "form", "password", "This field is required.")
        self.assertFormError(response, "form", "timezone", "This field is required.")

        # submit with invalid password and email
        post_data = dict(
            first_name="Eugene",
            last_name="Rwagasore",
            email="bad_email",
            password="badpass",
            name="Your Face",
            timezone="Africa/Kigali",
        )
        response = self.client.post(signup_url, post_data)
        self.assertFormError(response, "form", "email", "Enter a valid email address.")
        self.assertFormError(
            response, "form", "password", "This password is too short. It must contain at least 8 characters."
        )

        # submit with password that is too common
        post_data["email"] = "eugene@temba.io"
        post_data["password"] = "password"
        response = self.client.post(signup_url, post_data)
        self.assertFormError(response, "form", "password", "This password is too common.")

        # submit with password that is all numerical
        post_data["password"] = "3464357358532"
        response = self.client.post(signup_url, post_data)
        self.assertFormError(response, "form", "password", "This password is entirely numeric.")

        # submit with valid data (long email)
        post_data = dict(
            first_name="Eugene",
            last_name="Rwagasore",
            email="myal12345678901234567890@relieves.org",
            password="HelloWorld1",
            name="Relieves World",
            timezone="Africa/Kigali",
        )
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
        self.assertEqual(org.timezone, pytz.timezone("Africa/Kigali"))
        self.assertEqual(str(org), "Relieves World")
        self.assertTrue(org.uses_topups)

        # of which our user is an administrator
        self.assertTrue(org.get_admins().filter(pk=user.pk))

        # org should have 1000 credits
        self.assertEqual(org.get_credits_remaining(), 1000)

        # from a single welcome topup
        topup = TopUp.objects.get(org=org)
        self.assertEqual(topup.credits, 1000)
        self.assertEqual(topup.price, 0)

        # check default org content was created correctly
        system_fields = list(org.contactfields(manager="system_fields").order_by("key").values_list("key", flat=True))
        system_groups = list(org.all_groups(manager="system_groups").order_by("name").values_list("name", flat=True))
        sample_flows = list(org.flows.order_by("name").values_list("name", flat=True))
        internal_ticketer = org.ticketers.get()

        self.assertEqual(["created_on", "id", "language", "last_seen_on", "name"], system_fields)
        self.assertEqual(["Active", "Archived", "Blocked", "Stopped"], system_groups)
        self.assertEqual(
            ["Sample Flow - Order Status Checker", "Sample Flow - Satisfaction Survey", "Sample Flow - Simple Poll"],
            sample_flows,
        )
        self.assertEqual("RapidPro Tickets", internal_ticketer.name)

        # fake session set_org to make the test work
        user.set_org(org)

        # should now be able to go to channels page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertEqual(200, response.status_code)

        # check that we have all the tabs
        self.assertContains(response, reverse("msgs.msg_inbox"))
        self.assertContains(response, reverse("flows.flow_list"))
        self.assertContains(response, reverse("contacts.contact_list"))
        self.assertContains(response, reverse("channels.channel_list"))
        self.assertContains(response, reverse("orgs.org_home"))

        post_data["name"] = "Relieves World Rwanda"
        response = self.client.post(signup_url, post_data)
        self.assertIn("email", response.context["form"].errors)

        # if we hit /login we'll be taken back to the channel page
        response = self.client.get(reverse("users.user_check_login"))
        self.assertRedirect(response, reverse("orgs.org_choose"))

        # but if we log out, same thing takes us to the login page
        self.client.logout()

        response = self.client.get(reverse("users.user_check_login"))
        self.assertRedirect(response, reverse("users.user_login"))

        # try going to the org home page, no dice
        response = self.client.get(reverse("orgs.org_home"))
        self.assertRedirect(response, reverse("users.user_login"))

        # log in as the user
        self.client.login(username="myal12345678901234567890@relieves.org", password="HelloWorld1")
        response = self.client.get(reverse("orgs.org_home"))

        self.assertEqual(200, response.status_code)

        # try changing our username, wrong password
        post_data = dict(email="myal@wr.org", current_password="HelloWorld")
        response = self.client.post(reverse("orgs.user_edit"), post_data)
        self.assertEqual(200, response.status_code)
        self.assertIn("current_password", response.context["form"].errors)

        # bad new password
        post_data = dict(email="myal@wr.org", current_password="HelloWorld1", new_password="passwor")
        response = self.client.post(reverse("orgs.user_edit"), post_data)
        self.assertEqual(200, response.status_code)
        self.assertIn("new_password", response.context["form"].errors)

        User.objects.create(username="bill@msn.com", email="bill@msn.com")

        # dupe user
        post_data = dict(email="bill@msn.com", current_password="HelloWorld1")
        response = self.client.post(reverse("orgs.user_edit"), post_data)
        self.assertEqual(200, response.status_code)
        self.assertFormError(response, "form", "email", "Sorry, that email address is already taken.")

        post_data = dict(
            email="myal@wr.org",
            first_name="Myal",
            last_name="Greene",
            language="en-us",
            current_password="HelloWorld1",
        )
        response = self.client.post(reverse("orgs.user_edit"), post_data, HTTP_X_FORMAX=True)
        self.assertEqual(200, response.status_code)

        self.assertTrue(User.objects.get(username="myal@wr.org"))
        self.assertTrue(User.objects.get(email="myal@wr.org"))
        self.assertFalse(User.objects.filter(username="myal@relieves.org"))
        self.assertFalse(User.objects.filter(email="myal@relieves.org"))

        post_data["current_password"] = "HelloWorld1"
        post_data["new_password"] = "Password123"
        response = self.client.post(reverse("orgs.user_edit"), post_data, HTTP_X_FORMAX=True)
        self.assertEqual(200, response.status_code)

        user = User.objects.get(username="myal@wr.org")
        self.assertTrue(user.check_password("Password123"))

    def test_choose(self):
        choose_url = reverse("orgs.org_choose")

        # create an inactive org which should never appear as an option
        org3 = Org.objects.create(
            name="Deactivated",
            timezone=pytz.UTC,
            brand=settings.DEFAULT_BRAND,
            created_by=self.user,
            modified_by=self.user,
            is_active=False,
        )
        org3.editors.add(self.editor)

        # and another org that none of our users belong to
        org4 = Org.objects.create(
            name="Other", timezone=pytz.UTC, brand=settings.DEFAULT_BRAND, created_by=self.user, modified_by=self.user
        )

        self.assertLoginRedirect(self.client.get(choose_url))

        # users with a single org are always redirected right away to a page in that org that they have access to
        self.assertRedirect(self.requestView(choose_url, self.admin), "/msg/inbox/")
        self.assertRedirect(self.requestView(choose_url, self.editor), "/msg/inbox/")
        self.assertRedirect(self.requestView(choose_url, self.user), "/msg/inbox/")
        self.assertRedirect(self.requestView(choose_url, self.agent), "/ticket/")
        self.assertRedirect(self.requestView(choose_url, self.surveyor), "/org/surveyor/")

        # users with no org are redirected back to the login page
        response = self.requestView(choose_url, self.non_org_user)
        self.assertLoginRedirect(response)
        response = self.client.get("/users/login/")
        self.assertContains(response, "No organizations for this account, please contact your administrator.")

        # unless they are Customer Support
        Group.objects.get(name="Customer Support").user_set.add(self.non_org_user)
        self.assertRedirect(self.requestView(choose_url, self.non_org_user), "/org/manage/")

        # superusers are sent to the manage orgs page
        self.assertRedirect(self.requestView(choose_url, self.superuser), "/org/manage/")

        # turn editor into a multi-org user
        self.org2.editors.add(self.editor)

        # now we see a page to choose one of the two orgs
        response = self.requestView(choose_url, self.editor)
        self.assertEqual(["organization", "loc"], list(response.context["form"].fields.keys()))
        self.assertEqual({self.org, self.org2}, set(response.context["form"].fields["organization"].queryset))
        self.assertEqual({self.org, self.org2}, set(response.context["orgs"]))

        # try to submit for an org we don't belong to
        response = self.client.post(choose_url, {"organization": org4.id})
        self.assertFormError(
            response, "form", "organization", "Select a valid choice. That choice is not one of the available choices."
        )

        # user clicks org 2...
        response = self.client.post(choose_url, {"organization": self.org2.id})
        self.assertRedirect(response, "/msg/inbox/")

    def test_edit(self):
        edit_url = reverse("orgs.org_edit")

        self.assertLoginRedirect(self.client.get(edit_url))

        self.login(self.admin)
        response = self.client.get(edit_url)
        self.assertEqual(
            ["name", "timezone", "date_format", "language", "loc"], list(response.context["form"].fields.keys())
        )

        # try submitting with errors
        response = self.client.post(
            reverse("orgs.org_edit"),
            {"name": "", "timezone": "Bad/Timezone", "date_format": "X", "language": "klingon"},
        )
        self.assertFormError(response, "form", "name", "This field is required.")
        self.assertFormError(
            response, "form", "timezone", "Select a valid choice. Bad/Timezone is not one of the available choices."
        )
        self.assertFormError(
            response, "form", "date_format", "Select a valid choice. X is not one of the available choices."
        )
        self.assertFormError(
            response, "form", "language", "Select a valid choice. klingon is not one of the available choices."
        )

        response = self.client.post(
            reverse("orgs.org_edit"),
            {"name": "New Name", "timezone": "Africa/Nairobi", "date_format": "Y", "language": "es"},
        )
        self.assertEqual(302, response.status_code)

        self.org.refresh_from_db()
        self.assertEqual("New Name", self.org.name)
        self.assertEqual("Africa/Nairobi", str(self.org.timezone))
        self.assertEqual("Y", self.org.date_format)
        self.assertEqual("es", self.org.language)

    def test_org_timezone(self):
        self.assertEqual(self.org.timezone, pytz.timezone("Africa/Kigali"))
        self.assertEqual(("%d-%m-%Y", "%d-%m-%Y %H:%M"), self.org.get_datetime_formats())
        self.assertEqual(("%d-%m-%Y", "%d-%m-%Y %H:%M:%S"), self.org.get_datetime_formats(seconds=True))

        contact = self.create_contact("Bob", phone="+250788382382")
        self.create_incoming_msg(contact, "My name is Frank")

        self.login(self.admin)
        response = self.client.get(reverse("msgs.msg_inbox"), follow=True)

        # Check the message datetime
        created_on = response.context["object_list"][0].created_on.astimezone(self.org.timezone)
        self.assertContains(response, created_on.strftime("%H:%M").lower())

        # change the org timezone to "Africa/Nairobi"
        self.org.timezone = pytz.timezone("Africa/Nairobi")
        self.org.save()

        response = self.client.get(reverse("msgs.msg_inbox"), follow=True)

        # checkout the message should have the datetime changed by timezone
        created_on = response.context["object_list"][0].created_on.astimezone(self.org.timezone)
        self.assertContains(response, created_on.strftime("%H:%M").lower())

        self.org.date_format = "M"
        self.org.save()

        self.assertEqual(("%m-%d-%Y", "%m-%d-%Y %H:%M"), self.org.get_datetime_formats())

        response = self.client.get(reverse("msgs.msg_inbox"), follow=True)

        created_on = response.context["object_list"][0].created_on.astimezone(self.org.timezone)
        self.assertContains(response, created_on.strftime("%I:%M %p").lower().lstrip("0"))

        self.org.date_format = "Y"
        self.org.save()

        self.assertEqual(("%Y-%m-%d", "%Y-%m-%d %H:%M"), self.org.get_datetime_formats())

        response = self.client.get(reverse("msgs.msg_inbox"), follow=True)

        created_on = response.context["object_list"][0].created_on.astimezone(self.org.timezone)
        self.assertContains(response, created_on.strftime("%H:%M").lower())

    def test_administration(self):
        self.setUpLocations()

        def assert_superuser_only(mgmt_url):
            # no access to anon
            self.client.logout()
            self.assertLoginRedirect(self.client.get(mgmt_url))

            # or editors
            self.login(self.editor)
            self.assertLoginRedirect(self.client.get(mgmt_url))

            # or even admins
            self.login(self.admin)
            self.assertLoginRedirect(self.client.get(mgmt_url))

            # only superusers or staff
            self.login(self.superuser)
            response = self.client.get(mgmt_url)
            self.assertEqual(200, response.status_code)

        manage_url = reverse("orgs.org_manage")
        update_url = reverse("orgs.org_update", args=[self.org.id])
        delete_url = reverse("orgs.org_delete", args=[self.org.id])

        assert_superuser_only(manage_url)
        assert_superuser_only(update_url)
        assert_superuser_only(delete_url)

        response = self.client.get(manage_url + "?flagged=1")
        self.assertFalse(self.org in response.context["object_list"])

        response = self.client.get(manage_url + "?anon=1")
        self.assertFalse(self.org in response.context["object_list"])

        response = self.client.get(manage_url + "?suspended=1")
        self.assertFalse(self.org in response.context["object_list"])

        response = self.client.get(manage_url)
        self.assertEqual(200, response.status_code)
        self.assertNotContains(response, "(Flagged)")

        self.org.flag()
        response = self.client.get(manage_url)
        self.assertContains(response, "(Flagged)")

        # should contain our test org
        self.assertContains(response, "Temba")

        response = self.client.get(manage_url + "?flagged=1")
        self.assertTrue(self.org in response.context["object_list"])

        # and can go to that org
        response = self.client.get(update_url)
        self.assertEqual(200, response.status_code)

        # We should have the limits fields
        self.assertIn("fields_limit", response.context["form"].fields.keys())
        self.assertIn("globals_limit", response.context["form"].fields.keys())
        self.assertIn("groups_limit", response.context["form"].fields.keys())

        parent = Org.objects.create(
            name="Parent",
            timezone=pytz.timezone("Africa/Kigali"),
            country=self.country,
            brand=settings.DEFAULT_BRAND,
            created_by=self.user,
            modified_by=self.user,
        )

        # change to the trial plan
        response = self.client.post(
            update_url,
            {
                "name": "Temba",
                "brand": "rapidpro.io",
                "plan": "TRIAL",
                "plan_end": "",
                "language": "",
                "country": "",
                "primary_language": "",
                "timezone": pytz.timezone("Africa/Kigali"),
                "config": "{}",
                "date_format": "D",
                "parent": parent.id,
                "viewers": [self.user.id],
                "editors": [self.editor.id],
                "administrators": [self.admin.id],
                "surveyors": [self.surveyor.id],
                "surveyor_password": "",
                "fields_limit": 300,
                "groups_limit": 400,
            },
        )
        self.assertEqual(302, response.status_code)

        self.org.refresh_from_db()
        self.assertEqual(self.org.get_limit(Org.LIMIT_FIELDS), 300)
        self.assertEqual(self.org.get_limit(Org.LIMIT_GROUPS), 400)

        # reset groups limit
        post_data = {
            "name": "Temba",
            "brand": "rapidpro.io",
            "plan": "TRIAL",
            "plan_end": "",
            "language": "",
            "country": "",
            "primary_language": "",
            "timezone": pytz.timezone("Africa/Kigali"),
            "config": "{}",
            "date_format": "D",
            "parent": parent.id,
            "viewers": [self.user.id],
            "editors": [self.editor.id],
            "administrators": [self.admin.id],
            "surveyors": [self.surveyor.id],
            "surveyor_password": "",
            "fields_limit": 300,
            "groups_limit": "",
        }

        response = self.client.post(update_url, post_data)
        self.assertEqual(302, response.status_code)

        self.org.refresh_from_db()
        self.assertEqual(self.org.get_limit(Org.LIMIT_FIELDS), 300)
        self.assertEqual(self.org.get_limit(Org.LIMIT_GROUPS), 250)

        # unflag org
        post_data["action"] = "unflag"
        self.client.post(update_url, post_data)
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_flagged)
        self.assertEqual(parent, self.org.parent)

        # verify
        post_data["action"] = "verify"
        self.client.post(update_url, post_data)
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_verified())

        # flag org
        post_data["action"] = "flag"
        self.client.post(update_url, post_data)
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_flagged)

        # schedule for deletion
        response = self.client.get(delete_url, {"id": self.org.id})
        self.assertContains(response, "This will schedule deletion of <b>Temba</b>")

        response = self.client.post(delete_url, {"id": self.org.id})
        self.assertEqual(update_url, response["Temba-Success"])

        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)

        response = self.client.get(update_url)
        self.assertContains(response, "This workspace has been scheduled for deletion")

    def test_urn_schemes(self):
        # remove existing channels
        Channel.objects.all().update(is_active=False, org=None)

        self.assertEqual(set(), self.org.get_schemes(Channel.ROLE_SEND))
        self.assertEqual(set(), self.org.get_schemes(Channel.ROLE_RECEIVE))

        # add a receive only tel channel
        self.create_channel("T", "Twilio", "0785551212", country="RW", role="R")

        self.org = Org.objects.get(id=self.org.id)
        self.assertEqual(set(), self.org.get_schemes(Channel.ROLE_SEND))
        self.assertEqual({URN.TEL_SCHEME}, self.org.get_schemes(Channel.ROLE_RECEIVE))
        self.assertEqual({URN.TEL_SCHEME}, self.org.get_schemes(Channel.ROLE_RECEIVE))  # from cache

        # add a send/receive tel channel
        self.create_channel("T", "Twilio", "0785553434", country="RW", role="SR")

        self.org = Org.objects.get(pk=self.org.id)
        self.assertEqual({URN.TEL_SCHEME}, self.org.get_schemes(Channel.ROLE_SEND))
        self.assertEqual({URN.TEL_SCHEME}, self.org.get_schemes(Channel.ROLE_RECEIVE))

        # add a twitter channel
        self.create_channel("TT", "Twitter", "nyaruka")
        self.org = Org.objects.get(pk=self.org.id)
        self.assertEqual(
            {URN.TEL_SCHEME, URN.TWITTER_SCHEME, URN.TWITTERID_SCHEME}, self.org.get_schemes(Channel.ROLE_SEND)
        )
        self.assertEqual(
            {URN.TEL_SCHEME, URN.TWITTER_SCHEME, URN.TWITTERID_SCHEME}, self.org.get_schemes(Channel.ROLE_RECEIVE)
        )

    def test_login_case_not_sensitive(self):
        login_url = reverse("users.user_login")

        User.objects.create_superuser("superuser", "superuser@group.com", "superuser")

        response = self.client.post(login_url, dict(username="superuser", password="superuser"))
        self.assertEqual(response.status_code, 302)

        response = self.client.post(login_url, dict(username="superuser", password="superuser"), follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("orgs.org_manage"))

        response = self.client.post(login_url, dict(username="SUPeruser", password="superuser"))
        self.assertEqual(response.status_code, 302)

        response = self.client.post(login_url, dict(username="SUPeruser", password="superuser"), follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("orgs.org_manage"))

        User.objects.create_superuser("withCAPS", "with_caps@group.com", "thePASSWORD")

        response = self.client.post(login_url, dict(username="withcaps", password="thePASSWORD"))
        self.assertEqual(response.status_code, 302)

        response = self.client.post(login_url, dict(username="withcaps", password="thePASSWORD"), follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("orgs.org_manage"))

        # passwords stay case sensitive
        response = self.client.post(login_url, dict(username="withcaps", password="thepassword"), follow=True)
        self.assertIn("form", response.context)
        self.assertTrue(response.context["form"].errors)

    @mock_mailroom
    def test_org_service(self, mr_mocks):
        # create a customer service user
        self.csrep = self.create_user("csrep")
        self.csrep.groups.add(Group.objects.get(name="Customer Support"))
        self.csrep.is_staff = True
        self.csrep.save()

        service_url = reverse("orgs.org_service")

        # without logging in, try to service our main org
        response = self.client.post(service_url, dict(organization=self.org.id))
        self.assertRedirect(response, "/users/login/")

        # try logging in with a normal user
        self.login(self.admin)

        # same thing, no permission
        response = self.client.post(service_url, dict(organization=self.org.id))
        self.assertRedirect(response, "/users/login/")

        # ok, log in as our cs rep
        self.login(self.csrep)

        # then service our org
        response = self.client.post(service_url, dict(organization=self.org.id))
        self.assertRedirect(response, "/msg/inbox/")

        # specify redirect_url
        response = self.client.post(service_url, dict(organization=self.org.id, redirect_url="/flow/"))
        self.assertRedirect(response, "/flow/")

        # create a new contact
        response = self.client.post(
            reverse("contacts.contact_create"), data=dict(name="Ben Haggerty", urn__tel__0="0788123123")
        )
        self.assertNoFormErrors(response)

        # make sure that contact's created on is our cs rep
        contact = Contact.objects.get(urns__path="+250788123123", org=self.org)
        self.assertEqual(self.csrep, contact.created_by)

        # make sure we can manage topups as well
        TopUp.objects.create(
            org=self.org,
            price=100,
            credits=1000,
            expires_on=timezone.now() + timedelta(days=30),
            created_by=self.admin,
            modified_by=self.admin,
        )

        response = self.client.get(reverse("orgs.topup_manage") + "?org=%d" % self.org.id)

        # i'd buy that for a dollar!
        self.assertContains(response, "$1.00")
        self.assertNotRedirect(response, "/users/login/")

        # ok, now end our session
        response = self.client.post(service_url, dict())
        self.assertRedirect(response, "/org/manage/")

        # can no longer go to inbox, asked to log in
        response = self.client.get(reverse("msgs.msg_inbox"))
        self.assertRedirect(response, "/users/login/")

    def test_languages(self):
        home_url = reverse("orgs.org_home")
        langs_url = reverse("orgs.org_languages")

        self.org.set_flow_languages(self.admin, [])

        # check summary on home page
        response = self.requestView(home_url, self.admin)
        self.assertContains(response, "Your workspace is configured to use a single language.")

        self.assertUpdateFetch(
            langs_url,
            allow_viewers=False,
            allow_editors=False,
            allow_org2=True,  # is same URL across orgs
            form_fields=["primary_lang", "other_langs"],
        )

        # initial should do a match on code only
        response = self.client.get(f"{langs_url}?initial=fra", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual([{"name": "French", "value": "fra"}], response.json()["results"])

        # try to submit as is (empty)
        self.assertUpdateSubmit(
            langs_url,
            {},
            object_unchanged=self.org,
            form_errors={"primary_lang": "This field is required."},
        )

        # give the org a primary language
        self.assertUpdateSubmit(langs_url, {"primary_lang": '{"name":"French", "value":"fra"}'})

        self.org.refresh_from_db()
        self.assertEqual(["fra"], self.org.flow_languages)

        # summary now includes this
        response = self.requestView(home_url, self.admin)
        self.assertContains(response, "The default flow language is <b>French</b>.")
        self.assertNotContains(response, "Translations are provided in")

        # and now give it additional languages
        self.assertUpdateSubmit(
            langs_url,
            {
                "primary_lang": '{"name":"French", "value":"fra"}',
                "other_langs": ['{"name":"Haitian", "value":"hat"}', '{"name":"Hausa", "value":"hau"}'],
            },
        )

        self.org.refresh_from_db()
        self.assertEqual(["fra", "hat", "hau"], self.org.flow_languages)

        response = self.requestView(home_url, self.admin)
        self.assertContains(response, "The default flow language is <b>French</b>.")
        self.assertContains(response, "Translations are provided in")
        self.assertContains(response, "<b>Hausa</b>")

        # searching languages should only return languages with 2-letter codes
        response = self.client.get("%s?search=Fr" % langs_url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(
            [
                {"value": "afr", "name": "Afrikaans"},
                {"value": "fra", "name": "French"},
                {"value": "fry", "name": "Western Frisian"},
            ],
            response.json()["results"],
        )

        # unless they're explicitly included in settings
        with override_settings(NON_ISO6391_LANGUAGES={"frc"}):
            languages.reload()
            response = self.client.get("%s?search=Fr" % langs_url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(
                [
                    {"value": "afr", "name": "Afrikaans"},
                    {"value": "frc", "name": "Cajun French"},
                    {"value": "fra", "name": "French"},
                    {"value": "fry", "name": "Western Frisian"},
                ],
                response.json()["results"],
            )

        languages.reload()


class BulkExportTest(TembaTest):
    def test_import_validation(self):
        # export must include version field
        with self.assertRaises(ValueError):
            self.org.import_app({"flows": []}, self.admin)

        # export version can't be older than Org.EARLIEST_IMPORT_VERSION
        with self.assertRaises(ValueError):
            self.org.import_app({"version": "2", "flows": []}, self.admin)

        # export version can't be newer than Org.CURRENT_EXPORT_VERSION
        with self.assertRaises(ValueError):
            self.org.import_app({"version": "21415", "flows": []}, self.admin)

    def test_trigger_dependency(self):
        # tests the case of us doing an export of only a single flow (despite dependencies) and making sure we
        # don't include the triggers of our dependent flows (which weren't exported)
        self.import_file("parent_child_trigger")

        parent = Flow.objects.filter(name="Parent Flow").first()

        self.login(self.admin)

        # export only the parent
        post_data = dict(flows=[parent.pk], campaigns=[])
        response = self.client.post(reverse("orgs.org_export"), post_data)

        exported = response.json()

        # shouldn't have any triggers
        self.assertFalse(exported["triggers"])

    def test_subflow_dependencies(self):
        self.import_file("subflow")

        parent = Flow.objects.filter(name="Parent Flow").first()
        child = Flow.objects.filter(name="Child Flow").first()
        self.assertIn(child, parent.flow_dependencies.all())

        self.login(self.admin)
        response = self.client.get(reverse("orgs.org_export"))

        soup = BeautifulSoup(response.content, "html.parser")
        group = str(soup.findAll("div", {"class": "exportables-grp"})[0])

        self.assertIn("Parent Flow", group)
        self.assertIn("Child Flow", group)

    def test_import_voice_flows_expiration_time(self):
        # all imported voice flows should have a max expiration time of 15 min
        self.get_flow("ivr")

        self.assertEqual(Flow.objects.filter(flow_type=Flow.TYPE_VOICE).count(), 1)
        voice_flow = Flow.objects.get(flow_type=Flow.TYPE_VOICE)
        self.assertEqual(voice_flow.name, "IVR Flow")
        self.assertEqual(voice_flow.expires_after_minutes, 15)

    def test_import(self):

        self.login(self.admin)

        post_data = dict(import_file=open("%s/test_flows/too_old.json" % settings.MEDIA_ROOT, "rb"))
        response = self.client.post(reverse("orgs.org_import"), post_data)
        self.assertFormError(
            response, "form", "import_file", "This file is no longer valid. Please export a new version and try again."
        )

        # try a file which can be migrated forwards
        response = self.client.post(
            reverse("orgs.org_import"),
            {"import_file": open("%s/test_flows/favorites_v4.json" % settings.MEDIA_ROOT, "rb")},
        )
        self.assertEqual(302, response.status_code)

        flow = self.org.flows.filter(name="Favorites").get()
        self.assertEqual(Flow.CURRENT_SPEC_VERSION, flow.version_number)

        # simulate an unexpected exception during import
        with patch("temba.triggers.models.Trigger.import_triggers") as validate:
            validate.side_effect = Exception("Unexpected Error")
            post_data = dict(import_file=open("%s/test_flows/new_mother.json" % settings.MEDIA_ROOT, "rb"))
            response = self.client.post(reverse("orgs.org_import"), post_data)
            self.assertFormError(response, "form", "import_file", "Sorry, your import file is invalid.")

            # trigger import failed, new flows that were added should get rolled back
            self.assertIsNone(Flow.objects.filter(org=self.org, name="New Mother").first())

        # test import using data that is not parsable
        junk_binary_data = io.BytesIO(b"\x00!\x00b\xee\x9dh^\x01\x00\x00\x04\x00\x02[Content_Types].xml \xa2\x04\x02(")
        post_data = dict(import_file=junk_binary_data)
        response = self.client.post(reverse("orgs.org_import"), post_data)
        self.assertFormError(response, "form", "import_file", "This file is not a valid flow definition file.")

        junk_json_data = io.BytesIO(b'{"key": "data')
        post_data = dict(import_file=junk_json_data)
        response = self.client.post(reverse("orgs.org_import"), post_data)
        self.assertFormError(response, "form", "import_file", "This file is not a valid flow definition file.")

    def test_import_campaign_with_translations(self):
        self.import_file("campaign_import_with_translations")

        campaign = Campaign.objects.all().first()
        event = campaign.events.all().first()

        self.assertEqual(event.message["swa"], "hello")
        self.assertEqual(event.message["eng"], "Hey")

        # base language for this flow is 'swa' despite our org languages being unset
        self.assertEqual(event.flow.base_language, "swa")

        flow_def = event.flow.get_definition()
        action = flow_def["nodes"][0]["actions"][0]

        self.assertEqual(action["text"], "hello")
        self.assertEqual(flow_def["localization"]["eng"][action["uuid"]]["text"], ["Hey"])

    def test_reimport(self):
        self.import_file("survey_campaign")

        campaign = Campaign.objects.filter(is_active=True).last()
        event = campaign.events.filter(is_active=True).last()

        # create a contact and place her into our campaign
        sally = self.create_contact("Sally", phone="+12345", fields={"survey_start": "10-05-2025 12:30:10"})
        campaign.group.contacts.add(sally)

        # importing it again shouldn't result in failures
        self.import_file("survey_campaign")

        # get our latest campaign and event
        new_campaign = Campaign.objects.filter(is_active=True).last()
        new_event = campaign.events.filter(is_active=True).last()

        # same campaign, but new event
        self.assertEqual(campaign.id, new_campaign.id)
        self.assertNotEqual(event.id, new_event.id)

    def test_import_mixed_flow_versions(self):
        self.import_file("mixed_versions")

        group = ContactGroup.user_groups.get(name="Survey Audience")

        child = Flow.objects.get(name="New Child")
        self.assertEqual(child.version_number, Flow.CURRENT_SPEC_VERSION)
        self.assertEqual(set(child.flow_dependencies.all()), set())
        self.assertEqual(set(child.group_dependencies.all()), {group})

        parent = Flow.objects.get(name="Legacy Parent")
        self.assertEqual(parent.version_number, Flow.CURRENT_SPEC_VERSION)
        self.assertEqual(set(parent.flow_dependencies.all()), {child})
        self.assertEqual(set(parent.group_dependencies.all()), set())

        dep_graph = self.org.generate_dependency_graph()
        self.assertEqual(dep_graph[child], {parent})
        self.assertEqual(dep_graph[parent], {child})

    def test_import_dependency_types(self):
        self.import_file("all_dependency_types")

        parent = Flow.objects.get(name="All Dep Types")
        child = Flow.objects.get(name="New Child")

        age = ContactField.user_fields.get(key="age", label="Age")  # created from expression reference
        gender = ContactField.user_fields.get(key="gender")  # created from action reference

        farmers = ContactGroup.user_groups.get(name="Farmers")
        self.assertNotEqual(str(farmers.uuid), "967b469b-fd34-46a5-90f9-40430d6db2a4")  # created with new UUID

        self.assertEqual(set(parent.flow_dependencies.all()), {child})
        self.assertEqual(set(parent.field_dependencies.all()), {age, gender})
        self.assertEqual(set(parent.group_dependencies.all()), {farmers})

    @patch("temba.mailroom.client.MailroomClient.flow_inspect")
    def test_import_flow_issues(self, mock_flow_inspect):
        mock_flow_inspect.side_effect = [
            {
                # first call is during import to find dependencies to map or create
                "dependencies": [{"key": "age", "name": "", "type": "field", "missing": False}],
                "issues": [],
                "results": [],
                "waiting_exits": [],
                "parent_refs": [],
            },
            {
                # second call is in save_revision and passes org to validate dependencies, but during import those
                # dependencies which didn't exist already are created in a transaction and mailroom can't see them
                "dependencies": [{"key": "age", "name": "", "type": "field", "missing": True}],
                "issues": [{"type": "missing_dependency"}],
                "results": [],
                "waiting_exits": [],
                "parent_refs": [],
            },
            {
                # final call is after new flows and dependencies have been committed so mailroom can see them
                "dependencies": [{"key": "age", "name": "", "type": "field", "missing": False}],
                "issues": [],
                "results": [],
                "waiting_exits": [],
                "parent_refs": [],
            },
        ]
        self.import_file("color")

        flow = Flow.objects.get()

        self.assertFalse(flow.has_issues)

    def test_import_missing_flow_dependency(self):
        # in production this would blow up validating the flow but we can't do that during tests
        self.import_file("parent_without_its_child")

        parent = Flow.objects.get(name="Single Parent")
        self.assertEqual(set(parent.flow_dependencies.all()), set())

        # create child with that name and re-import
        child1 = Flow.create(self.org, self.admin, "New Child", Flow.TYPE_MESSAGE)

        self.import_file("parent_without_its_child")
        self.assertEqual(set(parent.flow_dependencies.all()), {child1})

        # create child with that UUID and re-import
        child2 = Flow.create(
            self.org, self.admin, "New Child", Flow.TYPE_MESSAGE, uuid="a925453e-ad31-46bd-858a-e01136732181"
        )

        self.import_file("parent_without_its_child")
        self.assertEqual(set(parent.flow_dependencies.all()), {child2})

    def validate_flow_dependencies(self, definition):
        flow_info = mailroom.get_client().flow_inspect(self.org.id, definition)
        deps = flow_info["dependencies"]

        for dep in [d for d in deps if d["type"] == "field"]:
            self.assertTrue(
                ContactField.user_fields.filter(key=dep["key"]).exists(),
                msg=f"missing field[key={dep['key']}, name={dep['name']}]",
            )
        for dep in [d for d in deps if d["type"] == "flow"]:
            self.assertTrue(
                Flow.objects.filter(uuid=dep["uuid"]).exists(),
                msg=f"missing flow[uuid={dep['uuid']}, name={dep['name']}]",
            )
        for dep in [d for d in deps if d["type"] == "group"]:
            self.assertTrue(
                ContactGroup.user_groups.filter(uuid=dep["uuid"]).exists(),
                msg=f"missing group[uuid={dep['uuid']}, name={dep['name']}]",
            )

    def test_implicit_field_and_group_imports(self):
        """
        Tests importing flow definitions without fields and groups included in the export
        """
        data = self.get_import_json("cataclysm")

        del data["fields"]
        del data["groups"]

        with ESMockWithScroll():
            self.org.import_app(data, self.admin, site="http://rapidpro.io")

        flow = Flow.objects.get(name="Cataclysmic")
        self.validate_flow_dependencies(flow.get_definition())

        # we should have 5 groups (all static since we can only create static groups from group references)
        self.assertEqual(ContactGroup.user_groups.all().count(), 5)
        self.assertEqual(ContactGroup.user_groups.filter(query=None).count(), 5)

        # and so no fields created
        self.assertEqual(ContactField.user_fields.all().count(), 0)

    @mock_mailroom
    def test_implicit_field_and_explicit_group_imports(self, mr_mocks):
        """
        Tests importing flow definitions with groups included in the export but not fields
        """
        data = self.get_import_json("cataclysm")
        del data["fields"]

        mr_mocks.parse_query("facts_per_day = 1", fields=["facts_per_day"])
        mr_mocks.parse_query("likes_cats = true", cleaned='likes_cats = "true"', fields=["likes_cats"])

        self.org.import_app(data, self.admin, site="http://rapidpro.io")

        flow = Flow.objects.get(name="Cataclysmic")
        self.validate_flow_dependencies(flow.get_definition())

        # we should have 5 groups (2 dynamic)
        self.assertEqual(ContactGroup.user_groups.all().count(), 5)
        self.assertEqual(ContactGroup.user_groups.filter(query=None).count(), 3)

        # new fields should have been created for the dynamic groups
        likes_cats = ContactField.user_fields.get(key="likes_cats")
        facts_per_day = ContactField.user_fields.get(key="facts_per_day")

        # but without implicit fields in the export, the details aren't correct
        self.assertEqual(likes_cats.label, "Likes Cats")
        self.assertEqual(likes_cats.value_type, "T")
        self.assertEqual(facts_per_day.label, "Facts Per Day")
        self.assertEqual(facts_per_day.value_type, "T")

        cat_fanciers = ContactGroup.user_groups.get(name="Cat Fanciers")
        self.assertEqual(cat_fanciers.query, 'likes_cats = "true"')
        self.assertEqual(set(cat_fanciers.query_fields.all()), {likes_cats})

        cat_blasts = ContactGroup.user_groups.get(name="Cat Blasts")
        self.assertEqual(cat_blasts.query, "facts_per_day = 1")
        self.assertEqual(set(cat_blasts.query_fields.all()), {facts_per_day})

    @mock_mailroom
    def test_explicit_field_and_group_imports(self, mr_mocks):
        """
        Tests importing flow definitions with groups and fields included in the export
        """

        mr_mocks.parse_query("facts_per_day = 1", fields=["facts_per_day"])
        mr_mocks.parse_query("likes_cats = true", cleaned='likes_cats = "true"', fields=["likes_cats"])

        self.import_file("cataclysm")

        flow = Flow.objects.get(name="Cataclysmic")
        self.validate_flow_dependencies(flow.get_definition())

        # we should have 5 groups (2 dynamic)
        self.assertEqual(ContactGroup.user_groups.all().count(), 5)
        self.assertEqual(ContactGroup.user_groups.filter(query=None).count(), 3)

        # new fields should have been created for the dynamic groups
        likes_cats = ContactField.user_fields.get(key="likes_cats")
        facts_per_day = ContactField.user_fields.get(key="facts_per_day")

        # and with implicit fields in the export, the details should be correct
        self.assertEqual(likes_cats.label, "Really Likes Cats")
        self.assertEqual(likes_cats.value_type, "T")
        self.assertEqual(facts_per_day.label, "Facts-Per-Day")
        self.assertEqual(facts_per_day.value_type, "N")

        cat_fanciers = ContactGroup.user_groups.get(name="Cat Fanciers")
        self.assertEqual(cat_fanciers.query, 'likes_cats = "true"')
        self.assertEqual(set(cat_fanciers.query_fields.all()), {likes_cats})

        cat_blasts = ContactGroup.user_groups.get(name="Cat Blasts")
        self.assertEqual(cat_blasts.query, "facts_per_day = 1")
        self.assertEqual(set(cat_blasts.query_fields.all()), {facts_per_day})

    def test_import_flow_with_triggers(self):
        flow1 = self.create_flow()
        flow2 = self.create_flow()

        trigger1 = Trigger.create(
            self.org, self.admin, Trigger.TYPE_KEYWORD, flow1, keyword="rating", is_archived=True
        )
        trigger2 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow2, keyword="rating")

        data = self.get_import_json("rating_10")

        with ESMockWithScroll():
            self.org.import_app(data, self.admin, site="http://rapidpro.io")

        # trigger1.refresh_from_db()
        # self.assertFalse(trigger1.is_archived)

        flow = Flow.objects.get(name="Rate us")
        self.assertEqual(1, Trigger.objects.filter(keyword="rating", is_archived=False).count())
        self.assertEqual(1, Trigger.objects.filter(flow=flow).count())

        # shoud have archived the existing
        self.assertFalse(Trigger.objects.filter(id=trigger1.id, is_archived=False).first())
        self.assertFalse(Trigger.objects.filter(id=trigger2.id, is_archived=False).first())

        # Archive trigger
        flow_trigger = (
            Trigger.objects.filter(flow=flow, keyword="rating", is_archived=False).order_by("-created_on").first()
        )
        flow_trigger.archive(self.admin)

        # re import again will restore the trigger
        data = self.get_import_json("rating_10")
        with ESMockWithScroll():
            self.org.import_app(data, self.admin, site="http://rapidpro.io")

        flow_trigger.refresh_from_db()

        self.assertEqual(1, Trigger.objects.filter(keyword="rating", is_archived=False).count())
        self.assertEqual(1, Trigger.objects.filter(flow=flow).count())
        self.assertFalse(Trigger.objects.filter(pk=trigger1.pk, is_archived=False).first())
        self.assertFalse(Trigger.objects.filter(pk=trigger2.pk, is_archived=False).first())

        restored_trigger = (
            Trigger.objects.filter(flow=flow, keyword="rating", is_archived=False).order_by("-created_on").first()
        )
        self.assertEqual(restored_trigger.pk, flow_trigger.pk)

    def test_export_import(self):
        def assert_object_counts():
            # the regular flows
            self.assertEqual(
                8,
                Flow.objects.filter(
                    org=self.org, is_active=True, is_archived=False, flow_type="M", is_system=False
                ).count(),
            )
            # the campaign single message flows
            self.assertEqual(
                2,
                Flow.objects.filter(
                    org=self.org, is_active=True, is_archived=False, flow_type="B", is_system=True
                ).count(),
            )
            self.assertEqual(1, Campaign.objects.filter(org=self.org, is_archived=False).count())
            self.assertEqual(
                4, CampaignEvent.objects.filter(campaign__org=self.org, event_type="F", is_active=True).count()
            )
            self.assertEqual(
                2, CampaignEvent.objects.filter(campaign__org=self.org, event_type="M", is_active=True).count()
            )
            self.assertEqual(2, Trigger.objects.filter(org=self.org, trigger_type="K", is_archived=False).count())
            self.assertEqual(1, Trigger.objects.filter(org=self.org, trigger_type="C", is_archived=False).count())
            self.assertEqual(1, Trigger.objects.filter(org=self.org, trigger_type="M", is_archived=False).count())
            self.assertEqual(3, ContactGroup.user_groups.filter(org=self.org).count())
            self.assertEqual(1, Label.label_objects.filter(org=self.org).count())
            self.assertEqual(
                1, ContactField.user_fields.filter(org=self.org, value_type="D", label="Next Appointment").count()
            )

        # import all our bits
        self.import_file("the_clinic")

        confirm_appointment = Flow.objects.get(name="Confirm Appointment")
        self.assertEqual(10080, confirm_appointment.expires_after_minutes)

        # check that the right number of objects successfully imported for our app
        assert_object_counts()

        # let's update some stuff
        confirm_appointment.expires_after_minutes = 360
        confirm_appointment.save(update_fields=("expires_after_minutes",))

        trigger = Trigger.objects.filter(keyword="patient").first()
        trigger.flow = confirm_appointment
        trigger.save()

        message_flow = (
            Flow.objects.filter(flow_type="B", is_system=True, campaign_events__offset=-1).order_by("id").first()
        )
        message_flow.update_single_message_flow(self.admin, {"base": "No reminders for you!"}, base_language="base")

        # now reimport
        self.import_file("the_clinic")

        # our flow should get reset from the import
        confirm_appointment.refresh_from_db()
        self.assertEqual(10080, confirm_appointment.expires_after_minutes)

        # same with our trigger
        trigger = Trigger.objects.filter(keyword="patient").order_by("-created_on").first()
        self.assertEqual(Flow.objects.filter(name="Register Patient").first(), trigger.flow)

        # our old campaign message flow should be inactive now
        self.assertTrue(Flow.objects.filter(pk=message_flow.pk, is_active=False))

        # find our new message flow, and see that the original message is there
        message_flow = (
            Flow.objects.filter(flow_type="B", is_system=True, campaign_events__offset=-1, is_active=True)
            .order_by("id")
            .first()
        )

        self.assertEqual(
            message_flow.get_definition()["nodes"][0]["actions"][0]["text"],
            "Hi there, just a quick reminder that you have an appointment at The Clinic at @(format_date(contact.next_appointment)). If you can't make it please call 1-888-THE-CLINIC.",
        )

        # and we should have the same number of items as after the first import
        assert_object_counts()

        # see that everything shows up properly on our export page
        self.login(self.admin)
        response = self.client.get(reverse("orgs.org_export"))
        self.assertContains(response, "Register Patient")
        self.assertContains(response, "Catch All")
        self.assertContains(response, "Missed Call")
        self.assertContains(response, "Start Notifications")
        self.assertContains(response, "Stop Notifications")
        self.assertContains(response, "Confirm Appointment")
        self.assertContains(response, "Appointment Followup")

        # our campaign
        self.assertContains(response, "Appointment Schedule")
        self.assertNotContains(
            response, "&quot;Appointment Schedule&quot;"
        )  # previous bug rendered campaign names incorrectly

        confirm_appointment.expires_after_minutes = 60
        confirm_appointment.save(update_fields=("expires_after_minutes",))

        # now let's export!
        post_data = dict(
            flows=[f.pk for f in Flow.objects.filter(flow_type="M", is_system=False)],
            campaigns=[c.pk for c in Campaign.objects.all()],
        )

        response = self.client.post(reverse("orgs.org_export"), post_data)
        exported = response.json()
        self.assertEqual(exported["version"], Org.CURRENT_EXPORT_VERSION)
        self.assertEqual(exported["site"], "https://app.rapidpro.io")

        self.assertEqual(8, len(exported.get("flows", [])))
        self.assertEqual(4, len(exported.get("triggers", [])))
        self.assertEqual(1, len(exported.get("campaigns", [])))
        self.assertEqual(
            exported["fields"],
            [
                {"key": "appointment_confirmed", "name": "Appointment Confirmed", "type": "text"},
                {"key": "next_appointment", "name": "Next Appointment", "type": "datetime"},
                {"key": "rating", "name": "Rating", "type": "text"},
            ],
        )
        self.assertEqual(
            exported["groups"],
            [
                {"uuid": matchers.UUID4String(), "name": "Delay Notification", "query": None},
                {"uuid": matchers.UUID4String(), "name": "Pending Appointments", "query": None},
                {"uuid": matchers.UUID4String(), "name": "Unsatisfied Customers", "query": None},
            ],
        )

        # set our default flow language to english
        self.org.set_flow_languages(self.admin, ["eng", "fra"])

        # finally let's try importing our exported file
        self.org.import_app(exported, self.admin, site="http://app.rapidpro.io")
        assert_object_counts()

        message_flow = (
            Flow.objects.filter(flow_type="B", is_system=True, campaign_events__offset=-1, is_active=True)
            .order_by("id")
            .first()
        )

        # make sure the base language is set to 'base', not 'eng'
        self.assertEqual(message_flow.base_language, "base")

        # let's rename a flow and import our export again
        flow = Flow.objects.get(name="Confirm Appointment")
        flow.name = "A new flow"
        flow.save(update_fields=("name",))

        campaign = Campaign.objects.get()
        campaign.name = "A new campaign"
        campaign.save(update_fields=("name",))

        group = ContactGroup.user_groups.get(name="Pending Appointments")
        group.name = "A new group"
        group.save(update_fields=("name",))

        # it should fall back on UUIDs and not create new objects even though the names changed
        self.org.import_app(exported, self.admin, site="http://app.rapidpro.io")

        assert_object_counts()

        # and our objects should have the same names as before
        self.assertEqual("Confirm Appointment", Flow.objects.get(pk=flow.pk).name)
        self.assertEqual("Appointment Schedule", Campaign.objects.filter(is_active=True).first().name)
        self.assertEqual("Pending Appointments", ContactGroup.user_groups.get(pk=group.pk).name)

        # let's rename our objects again
        flow.name = "A new name"
        flow.save(update_fields=("name",))

        campaign.name = "A new campaign"
        campaign.save(update_fields=("name",))

        group.name = "A new group"
        group.save(update_fields=("name",))

        # now import the same import but pretend its from a different site
        self.org.import_app(exported, self.admin, site="http://temba.io")

        # the newly named objects won't get updated in this case and we'll create new ones instead
        self.assertEqual(
            9, Flow.objects.filter(org=self.org, is_archived=False, flow_type="M", is_system=False).count()
        )
        self.assertEqual(2, Campaign.objects.filter(org=self.org, is_archived=False).count())
        self.assertEqual(4, ContactGroup.user_groups.filter(org=self.org).count())

        # now archive a flow
        register = Flow.objects.filter(name="Register Patient").first()
        register.is_archived = True
        register.save()

        # default view shouldn't show archived flows
        response = self.client.get(reverse("orgs.org_export"))
        self.assertNotContains(response, "Register Patient")

        # with the archived flag one, it should be there
        response = self.client.get("%s?archived=1" % reverse("orgs.org_export"))
        self.assertContains(response, "Register Patient")

        # delete our flow, and reimport
        confirm_appointment.release(self.admin)
        self.org.import_app(exported, self.admin, site="https://app.rapidpro.io")

        # make sure we have the previously exported expiration
        confirm_appointment = Flow.objects.get(name="Confirm Appointment", is_active=True)
        self.assertEqual(60, confirm_appointment.expires_after_minutes)

        # should be unarchived
        register = Flow.objects.filter(name="Register Patient").first()
        self.assertFalse(register.is_archived)

        # now delete a flow
        register.is_active = False
        register.save()

        # default view shouldn't show deleted flows
        response = self.client.get(reverse("orgs.org_export"))
        self.assertNotContains(response, "Register Patient")

        # even with the archived flag one deleted flows should not show up
        response = self.client.get("%s?archived=1" % reverse("orgs.org_export"))
        self.assertNotContains(response, "Register Patient")


class CreditAlertTest(TembaTest):
    @override_settings(HOSTNAME="rapidpro.io", SEND_EMAILS=True)
    def test_check_topup_expiration(self):
        from .tasks import check_topup_expiration_task

        # get the topup, it expires in a year by default
        topup = self.org.topups.order_by("-expires_on").first()

        # there are no credit alerts
        self.assertFalse(CreditAlert.objects.filter(org=self.org, alert_type=CreditAlert.TYPE_EXPIRING))

        # check if credit alerts should be created
        check_topup_expiration_task()

        # no alert since no expiring credits
        self.assertFalse(CreditAlert.objects.filter(org=self.org, alert_type=CreditAlert.TYPE_EXPIRING))

        # update topup to expire in 10 days
        topup.expires_on = timezone.now() + timedelta(days=10)
        topup.save(update_fields=("expires_on",))

        # create another expiring topup, newer than the most recent one
        TopUp.create(self.admin, 1000, 9876, expires_on=timezone.now() + timedelta(days=25), org=self.org)

        # set the org to not use topups
        Org.objects.filter(id=self.org.id).update(uses_topups=False)

        # recheck the expiration
        check_topup_expiration_task()

        # no alert since we don't use topups
        self.assertFalse(CreditAlert.objects.filter(org=self.org, alert_type=CreditAlert.TYPE_EXPIRING))

        # switch batch and recalculate again
        Org.objects.filter(id=self.org.id).update(uses_topups=True)
        check_topup_expiration_task()

        # expiring credit alert created and email sent
        self.assertEqual(
            CreditAlert.objects.filter(is_active=True, org=self.org, alert_type=CreditAlert.TYPE_EXPIRING).count(), 1
        )

        self.assertEqual(len(mail.outbox), 1)

        # email sent
        sent_email = mail.outbox[0]
        self.assertEqual(1, len(sent_email.to))
        self.assertIn("RapidPro workspace for Temba", sent_email.body)
        self.assertIn("expiring credits in less than one month.", sent_email.body)

        # check topup expiration, it should no create a new one, because last one is still active
        check_topup_expiration_task()

        # no new alrets, and no new emails have been sent
        self.assertEqual(
            CreditAlert.objects.filter(is_active=True, org=self.org, alert_type=CreditAlert.TYPE_EXPIRING).count(), 1
        )
        self.assertEqual(len(mail.outbox), 1)

        # reset alerts, this is normal procedure after someone adds a new topup
        CreditAlert.reset_for_org(self.org)
        self.assertFalse(CreditAlert.objects.filter(org=self.org, is_active=True))

        # check topup expiration, it should create a new topup alert email
        check_topup_expiration_task()

        self.assertEqual(
            CreditAlert.objects.filter(is_active=True, org=self.org, alert_type=CreditAlert.TYPE_EXPIRING).count(), 1
        )
        self.assertEqual(len(mail.outbox), 2)

    def test_creditalert_sendemail_all_org_admins(self):
        # add some administrators to the org
        self.org.administrators.add(self.user)
        self.org.administrators.add(self.surveyor)

        # create a CreditAlert
        creditalert = CreditAlert.objects.create(
            org=self.org, alert_type=CreditAlert.TYPE_EXPIRING, created_by=self.admin, modified_by=self.admin
        )
        with self.settings(HOSTNAME="rapidpro.io", SEND_EMAILS=True):
            creditalert.send_email()

            self.assertEqual(len(mail.outbox), 1)

            sent_email = mail.outbox[0]
            self.assertIn("RapidPro workspace for Temba", sent_email.body)

            # this email has been sent to multiple recipients
            self.assertListEqual(
                sent_email.recipients(), ["Administrator@nyaruka.com", "Surveyor@nyaruka.com", "User@nyaruka.com"]
            )

    def test_creditalert_sendemail_no_org_admins(self):
        # remove administrators from org
        self.org.administrators.clear()

        # create a CreditAlert
        creditalert = CreditAlert.objects.create(
            org=self.org, alert_type=CreditAlert.TYPE_EXPIRING, created_by=self.admin, modified_by=self.admin
        )
        with self.settings(HOSTNAME="rapidpro.io", SEND_EMAILS=True):
            creditalert.send_email()

            # no emails have been sent
            self.assertEqual(len(mail.outbox), 0)

    def test_check_org_credits(self):
        self.joe = self.create_contact("Joe Blow", phone="123")
        self.create_outgoing_msg(self.joe, "Hello")
        with self.settings(HOSTNAME="rapidpro.io", SEND_EMAILS=True):
            with patch("temba.orgs.models.Org.get_credits_remaining") as mock_get_credits_remaining:
                mock_get_credits_remaining.return_value = -1

                # no alert yet
                self.assertFalse(CreditAlert.objects.all())

                CreditAlert.check_org_credits()

                # one alert created and sent
                self.assertEqual(
                    1,
                    CreditAlert.objects.filter(is_active=True, org=self.org, alert_type=CreditAlert.TYPE_OVER).count(),
                )
                self.assertEqual(1, len(mail.outbox))

                # alert email is for out of credits type
                sent_email = mail.outbox[0]
                self.assertEqual(len(sent_email.to), 1)
                self.assertIn("RapidPro workspace for Temba", sent_email.body)
                self.assertIn("is out of credit.", sent_email.body)

                # no new alert if one is sent and no new email
                CreditAlert.check_org_credits()
                self.assertEqual(
                    1,
                    CreditAlert.objects.filter(is_active=True, org=self.org, alert_type=CreditAlert.TYPE_OVER).count(),
                )
                self.assertEqual(1, len(mail.outbox))

                # reset alerts
                CreditAlert.reset_for_org(self.org)
                self.assertFalse(CreditAlert.objects.filter(org=self.org, is_active=True))

                # can resend a new alert
                CreditAlert.check_org_credits()
                self.assertEqual(
                    1,
                    CreditAlert.objects.filter(is_active=True, org=self.org, alert_type=CreditAlert.TYPE_OVER).count(),
                )
                self.assertEqual(2, len(mail.outbox))

                mock_get_credits_remaining.return_value = 10

                with patch("temba.orgs.models.Org.has_low_credits") as mock_has_low_credits:
                    mock_has_low_credits.return_value = True

                    self.assertFalse(CreditAlert.objects.filter(org=self.org, alert_type=CreditAlert.TYPE_LOW))

                    CreditAlert.check_org_credits()

                    # low credit alert created and email sent
                    self.assertEqual(
                        1,
                        CreditAlert.objects.filter(
                            is_active=True, org=self.org, alert_type=CreditAlert.TYPE_LOW
                        ).count(),
                    )
                    self.assertEqual(3, len(mail.outbox))

                    # email sent
                    sent_email = mail.outbox[2]
                    self.assertEqual(len(sent_email.to), 1)
                    self.assertIn("RapidPro workspace for Temba", sent_email.body)
                    self.assertIn("is running low on credits", sent_email.body)

                    # no new alert if one is sent and no new email
                    CreditAlert.check_org_credits()
                    self.assertEqual(
                        1,
                        CreditAlert.objects.filter(
                            is_active=True, org=self.org, alert_type=CreditAlert.TYPE_LOW
                        ).count(),
                    )
                    self.assertEqual(3, len(mail.outbox))

                    # reset alerts
                    CreditAlert.reset_for_org(self.org)
                    self.assertFalse(CreditAlert.objects.filter(org=self.org, is_active=True))

                    # can resend a new alert
                    CreditAlert.check_org_credits()
                    self.assertEqual(
                        1,
                        CreditAlert.objects.filter(
                            is_active=True, org=self.org, alert_type=CreditAlert.TYPE_LOW
                        ).count(),
                    )
                    self.assertEqual(4, len(mail.outbox))

                    mock_has_low_credits.return_value = False


class StripeCreditsTest(TembaTest):
    @patch("stripe.Customer.create")
    @patch("stripe.Charge.create")
    @override_settings(SEND_EMAILS=True)
    def test_add_credits(self, charge_create, customer_create):
        customer_create.return_value = dict_to_struct("Customer", dict(id="stripe-cust-1"))
        charge_create.return_value = dict_to_struct(
            "Charge",
            dict(id="stripe-charge-1", card=dict_to_struct("Card", dict(last4="1234", type="Visa", name="Rudolph"))),
        )

        settings.BRANDING[settings.DEFAULT_BRAND]["bundles"] = (dict(cents="2000", credits=1000, feature=""),)

        self.assertTrue(1000, self.org.get_credits_total())
        self.org.add_credits("2000", "stripe-token", self.admin)
        self.assertTrue(2000, self.org.get_credits_total())

        # assert we saved our charge info
        topup = self.org.topups.last()
        self.assertEqual("stripe-charge-1", topup.stripe_charge)

        # and we saved our stripe customer info
        org = Org.objects.get(id=self.org.id)
        self.assertEqual("stripe-cust-1", org.stripe_customer)

        # assert we sent our confirmation emai
        self.assertEqual(1, len(mail.outbox))
        email = mail.outbox[0]
        self.assertEqual("RapidPro Receipt", email.subject)
        self.assertIn("Rudolph", email.body)
        self.assertIn("Visa", email.body)
        self.assertIn("$20", email.body)

        # turn off email receipts and do it again, shouldn't get a receipt
        with override_settings(SEND_RECEIPTS=False):
            self.org.add_credits("2000", "stripe-token", self.admin)

            # no new emails
            self.assertEqual(1, len(mail.outbox))

    @patch("stripe.Customer.create")
    @patch("stripe.Charge.create")
    @override_settings(SEND_EMAILS=True)
    def test_add_btc_credits(self, charge_create, customer_create):
        customer_create.return_value = dict_to_struct("Customer", dict(id="stripe-cust-1"))
        charge_create.return_value = dict_to_struct(
            "Charge",
            dict(
                id="stripe-charge-1",
                card=None,
                source=dict_to_struct("Source", dict(bitcoin=dict_to_struct("Bitcoin", dict(address="abcde")))),
            ),
        )

        settings.BRANDING[settings.DEFAULT_BRAND]["bundles"] = (dict(cents="2000", credits=1000, feature=""),)

        self.org.add_credits("2000", "stripe-token", self.admin)
        self.assertTrue(2000, self.org.get_credits_total())

        # assert we saved our charge info
        topup = self.org.topups.last()
        self.assertEqual("stripe-charge-1", topup.stripe_charge)

        # and we saved our stripe customer info
        org = Org.objects.get(id=self.org.id)
        self.assertEqual("stripe-cust-1", org.stripe_customer)

        # assert we sent our confirmation emai
        self.assertEqual(1, len(mail.outbox))
        email = mail.outbox[0]
        self.assertEqual("RapidPro Receipt", email.subject)
        self.assertIn("bitcoin", email.body)
        self.assertIn("abcde", email.body)
        self.assertIn("$20", email.body)

    @patch("stripe.Customer.create")
    def test_add_credits_fail(self, customer_create):
        customer_create.side_effect = ValueError("Invalid customer token")

        with self.assertRaises(ValidationError):
            self.org.add_credits("2000", "stripe-token", self.admin)

        # assert no email was sent
        self.assertEqual(0, len(mail.outbox))

        # and no topups created
        self.assertEqual(1, self.org.topups.all().count())
        self.assertEqual(1000, self.org.get_credits_total())

    def test_add_credits_invalid_bundle(self):

        with self.assertRaises(ValidationError):
            self.org.add_credits("-10", "stripe-token", self.admin)

        # assert no email was sent
        self.assertEqual(0, len(mail.outbox))

        # and no topups created
        self.assertEqual(1, self.org.topups.all().count())
        self.assertEqual(1000, self.org.get_credits_total())

    @patch("stripe.Customer.create")
    @patch("stripe.Customer.retrieve")
    @patch("stripe.Charge.create")
    @override_settings(SEND_EMAILS=True)
    def test_add_credits_existing_customer(self, charge_create, customer_retrieve, customer_create):
        self.admin2 = self.create_user("Administrator 2")
        self.org.administrators.add(self.admin2)

        self.org.stripe_customer = "stripe-cust-1"
        self.org.save()

        class MockCard(object):
            def __init__(self):
                self.id = "stripe-card-1"

            def delete(self):
                pass

        class MockCards(object):
            def __init__(self):
                self.throw = False

            def list(self):
                return dict_to_struct("MockCardData", dict(data=[MockCard(), MockCard()]))

            def create(self, card):
                if self.throw:
                    raise stripe.error.CardError("Card declined", None, 400)
                else:
                    return MockCard()

        class MockCustomer(object):
            def __init__(self, id, email):
                self.id = id
                self.email = email
                self.cards = MockCards()

            def save(self):
                pass

        customer_retrieve.return_value = MockCustomer(id="stripe-cust-1", email=self.admin.email)
        customer_create.return_value = MockCustomer(id="stripe-cust-2", email=self.admin2.email)

        charge_create.return_value = dict_to_struct(
            "Charge",
            dict(id="stripe-charge-1", card=dict_to_struct("Card", dict(last4="1234", type="Visa", name="Rudolph"))),
        )

        settings.BRANDING[settings.DEFAULT_BRAND]["bundles"] = (dict(cents="2000", credits=1000, feature=""),)

        self.org.add_credits("2000", "stripe-token", self.admin)
        self.assertTrue(2000, self.org.get_credits_total())

        # assert we saved our charge info
        topup = self.org.topups.last()
        self.assertEqual("stripe-charge-1", topup.stripe_charge)

        # and we saved our stripe customer info
        org = Org.objects.get(id=self.org.id)
        self.assertEqual("stripe-cust-1", org.stripe_customer)

        # assert we sent our confirmation email
        self.assertEqual(1, len(mail.outbox))
        email = mail.outbox[0]
        self.assertEqual("RapidPro Receipt", email.subject)
        self.assertIn("Rudolph", email.body)
        self.assertIn("Visa", email.body)
        self.assertIn("$20", email.body)

        # try with an invalid card
        customer_retrieve.return_value.cards.throw = True
        try:
            self.org.add_credits("2000", "stripe-token", self.admin)
            self.fail("should have thrown")
        except ValidationError as e:
            self.assertEqual(
                "Sorry, your card was declined, please contact your provider or try another card.", e.message
            )

        # do it again with a different user, should create a new stripe customer
        self.org.add_credits("2000", "stripe-token", self.admin2)
        self.assertTrue(4000, self.org.get_credits_total())

        # should have a different customer now
        org = Org.objects.get(id=self.org.id)
        self.assertEqual("stripe-cust-2", org.stripe_customer)


class OrgActivityTest(TembaTest):
    def test_get_dependencies(self):
        from temba.orgs.tasks import update_org_activity

        now = timezone.now()

        # create a few contacts
        self.create_contact("Marshawn", phone="+14255551212")
        russell = self.create_contact("Marshawn", phone="+14255551313")

        # create some messages for russel
        self.create_incoming_msg(russell, "hut")
        self.create_incoming_msg(russell, "10-2")
        self.create_outgoing_msg(russell, "first down")

        # calculate our org activity, should get nothing because we aren't tomorrow yet
        update_org_activity(now)
        self.assertEqual(0, OrgActivity.objects.all().count())

        # ok, calculate based on a now of tomorrow, will calculate today's stats
        update_org_activity(now + timedelta(days=1))

        activity = OrgActivity.objects.get()
        self.assertEqual(2, activity.contact_count)
        self.assertEqual(1, activity.active_contact_count)
        self.assertEqual(2, activity.incoming_count)
        self.assertEqual(1, activity.outgoing_count)
        self.assertIsNone(activity.plan_active_contact_count)

        # set a plan start and plan end
        OrgActivity.objects.all().delete()
        self.org.plan_start = now
        self.org.plan_end = now + timedelta(days=30)
        self.org.save()

        update_org_activity(now + timedelta(days=1))
        activity = OrgActivity.objects.get()
        self.assertEqual(2, activity.contact_count)
        self.assertEqual(1, activity.active_contact_count)
        self.assertEqual(2, activity.incoming_count)
        self.assertEqual(1, activity.outgoing_count)
        self.assertEqual(1, activity.plan_active_contact_count)


class BackupTokenTest(TembaTest):
    def test_model(self):
        admin_tokens = BackupToken.generate_for_user(self.admin)
        BackupToken.generate_for_user(self.editor)

        self.assertEqual(10, len(admin_tokens))
        self.assertEqual(10, self.admin.backup_tokens.count())
        self.assertEqual(10, self.editor.backup_tokens.count())
        self.assertEqual(str(admin_tokens[0].token), str(admin_tokens[0]))

        # regenerate tokens for admin user
        new_admin_tokens = BackupToken.generate_for_user(self.admin)
        self.assertEqual(10, len(new_admin_tokens))
        self.assertNotEqual([t.token for t in admin_tokens], [t.token for t in new_admin_tokens])
        self.assertEqual(10, self.admin.backup_tokens.count())
