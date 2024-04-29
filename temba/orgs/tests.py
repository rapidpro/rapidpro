import io
import smtplib
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlencode

import pytz
from bs4 import BeautifulSoup
from smartmin.users.models import FailedLogin, RecoveryToken

from django.conf import settings
from django.contrib.auth.models import Group
from django.core import mail
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
from temba.notifications.types.builtin import ExportFinishedNotificationType
from temba.request_logs.models import HTTPLog
from temba.templates.models import Template, TemplateTranslation
from temba.tests import (
    CRUDLTestMixin,
    ESMockWithScroll,
    MigrationTest,
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
from temba.utils import brands, json, languages

from .context_processors import RolePermsWrapper
from .models import BackupToken, Invitation, Org, OrgMembership, OrgRole, User
from .tasks import delete_released_orgs, resume_failed_tasks


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
        perms = RolePermsWrapper(OrgRole.ADMINISTRATOR)

        self.assertTrue(perms["msgs"]["msg_list"])
        self.assertTrue(perms["contacts"]["contact_update"])
        self.assertTrue(perms["orgs"]["org_country"])
        self.assertTrue(perms["orgs"]["org_manage_accounts"])
        self.assertTrue(perms["orgs"]["org_delete"])

        perms = RolePermsWrapper(OrgRole.EDITOR)

        self.assertTrue(perms["msgs"]["msg_list"])
        self.assertTrue(perms["contacts"]["contact_update"])
        self.assertFalse(perms["orgs"]["org_manage_accounts"])
        self.assertFalse(perms["orgs"]["org_delete"])

        perms = RolePermsWrapper(OrgRole.VIEWER)

        self.assertTrue(perms["msgs"]["msg_list"])
        self.assertFalse(perms["contacts"]["contact_update"])
        self.assertFalse(perms["orgs"]["org_manage_accounts"])
        self.assertFalse(perms["orgs"]["org_delete"])

        self.assertFalse(perms["msgs"]["foo"])  # no blow up if perm doesn't exist
        self.assertFalse(perms["chickens"]["foo"])  # or app doesn't exist

        with self.assertRaises(TypeError):
            list(perms)


class UserTest(TembaTest):
    def test_model(self):
        user = User.create("jim@rapidpro.io", "Jim", "McFlow", password="super")
        self.org.add_user(user, OrgRole.EDITOR)
        self.org2.add_user(user, OrgRole.EDITOR)

        self.assertEqual("Jim McFlow", user.name)
        self.assertFalse(user.is_alpha)
        self.assertFalse(user.is_beta)
        self.assertEqual({"email": "jim@rapidpro.io", "name": "Jim McFlow"}, user.as_engine_ref())
        self.assertEqual([self.org, self.org2], list(user.get_orgs().order_by("id")))
        self.assertEqual([], list(user.get_orgs(roles=[OrgRole.ADMINISTRATOR]).order_by("id")))
        self.assertEqual([self.org, self.org2], list(user.get_orgs(roles=[OrgRole.EDITOR]).order_by("id")))

        user.last_name = ""
        user.save(update_fields=("last_name",))

        self.assertEqual("Jim", user.name)
        self.assertEqual({"email": "jim@rapidpro.io", "name": "Jim"}, user.as_engine_ref())

    def test_has_org_perm(self):
        granter = self.create_user("jim@rapidpro.io", group_names=("Granters",))

        tests = (
            (
                self.org,
                "contacts.contact_list",
                {
                    self.agent: False,
                    self.user: True,
                    self.admin: True,
                    self.admin2: False,
                    self.customer_support: True,
                },
            ),
            (
                self.org2,
                "contacts.contact_list",
                {
                    self.agent: False,
                    self.user: False,
                    self.admin: False,
                    self.admin2: True,
                    self.customer_support: True,
                },
            ),
            (
                self.org2,
                "contacts.contact_read",
                {
                    self.agent: False,
                    self.user: False,
                    self.admin: False,
                    self.admin2: True,
                    self.customer_support: True,  # needed for servicing
                },
            ),
            (
                self.org,
                "orgs.org_edit",
                {
                    self.agent: False,
                    self.user: False,
                    self.admin: True,
                    self.admin2: False,
                    self.customer_support: True,
                },
            ),
            (
                self.org2,
                "orgs.org_edit",
                {
                    self.agent: False,
                    self.user: False,
                    self.admin: False,
                    self.admin2: True,
                    self.customer_support: True,
                },
            ),
            (
                self.org,
                "orgs.org_grant",
                {
                    self.agent: False,
                    self.user: False,
                    self.admin: False,
                    self.admin2: False,
                    self.customer_support: True,
                    granter: True,
                },
            ),
            (
                self.org,
                "xxx.yyy_zzz",
                {
                    self.agent: False,
                    self.user: False,
                    self.admin: False,
                    self.admin2: False,
                    self.customer_support: True,  # staff have implicit all perm access
                },
            ),
        )
        for (org, perm, checks) in tests:
            for user, has_perm in checks.items():
                self.assertEqual(
                    has_perm,
                    user.has_org_perm(org, perm),
                    f"expected {user} to{'' if has_perm else ' not'} have perm {perm} in org {org.name}",
                )

    def test_login(self):
        login_url = reverse("users.user_login")
        verify_url = reverse("users.two_factor_verify")
        backup_url = reverse("users.two_factor_backup")

        self.assertIsNone(self.admin.settings.last_auth_on)

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
        response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "Qwerty123"})
        self.assertRedirect(response, reverse("orgs.org_choose"))

        del self.admin.settings  # clear cached_property
        self.assertIsNotNone(self.admin.settings.last_auth_on)

        # logout and enable 2FA
        self.client.logout()
        self.admin.enable_2fa()

        # can't access two-factor verify page yet
        response = self.client.get(verify_url)
        self.assertLoginRedirect(response)

        # login via login page again
        response = self.client.post(
            login_url + "?next=/msg/inbox/", {"username": "admin@nyaruka.com", "password": "Qwerty123"}
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
        response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "Qwerty123"})
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
        self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "pass123"})
        self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "pass123"})
        response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "pass123"})

        self.assertRedirect(response, failed_url)
        self.assertRedirect(self.client.get(reverse("msgs.msg_inbox")), login_url)

        # simulate failed logins timing out by making them older
        FailedLogin.objects.all().update(failed_on=timezone.now() - timedelta(minutes=3))

        # now we're allowed to make failed logins again
        response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "pass123"})
        self.assertFormError(
            response,
            "form",
            "__all__",
            "Please enter a correct username and password. Note that both fields may be case-sensitive.",
        )

        # and successful logins
        response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "Qwerty123"})
        self.assertRedirect(response, reverse("orgs.org_choose"))

        # try again with 2FA enabled
        self.client.logout()
        self.admin.enable_2fa()

        # submit incorrect username and password 3 times
        self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "pass123"})
        self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "pass123"})
        response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "pass123"})

        self.assertRedirect(response, failed_url)
        self.assertRedirect(self.client.get(reverse("msgs.msg_inbox")), login_url)

        # login correctly
        FailedLogin.objects.all().delete()
        response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "Qwerty123"})
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

        response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "Qwerty123"})
        self.assertRedirect(response, verify_url)

        response = self.client.post(backup_url, {"token": self.admin.backup_tokens.first()})
        self.assertRedirect(response, reverse("orgs.org_choose"))

    def test_account(self):
        self.login(self.admin)
        response = self.client.get(reverse("orgs.user_account"))
        self.assertEqual(1, len(response.context["formax"].sections))

    def test_two_factor(self):
        self.assertFalse(self.admin.settings.two_factor_enabled)

        self.admin.enable_2fa()

        self.assertTrue(self.admin.settings.two_factor_enabled)
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

        self.assertFalse(self.admin.settings.two_factor_enabled)

    def test_two_factor_spa(self):
        enable_url = reverse("orgs.user_two_factor_enable")
        tokens_url = reverse("orgs.user_two_factor_tokens")
        self.login(self.admin)

        # submit with valid OTP and password
        with patch("pyotp.TOTP.verify", return_value=True):
            response = self.client.post(enable_url, {"otp": "123456", "password": "Qwerty123"})

        header = {"HTTP_TEMBA_SPA": 1}
        response = self.client.get(tokens_url, **header)
        self.assertContains(response, "Regenerate Tokens")
        self.assertNotContains(response, "gear-container")

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
            response = self.client.post(enable_url, {"otp": "123456", "password": "Qwerty123"})
        self.assertRedirect(response, tokens_url)
        self.assertTrue(self.admin.settings.two_factor_enabled)

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
        response = self.client.post(disable_url, {"password": "Qwerty123"})
        self.assertRedirect(response, reverse("orgs.org_home"))

        del self.admin.settings  # clear cached_property
        self.assertFalse(self.admin.settings.two_factor_enabled)

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
            response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "Qwerty123"})
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
        response = self.client.post(confirm_url, {"password": "Qwerty123"})
        self.assertRedirect(response, tokens_url)

        response = self.client.get(tokens_url)
        self.assertEqual(200, response.status_code)

    @override_settings(USER_LOCKOUT_TIMEOUT=1, USER_FAILED_LOGIN_LIMIT=3)
    def test_confirm_access(self):
        confirm_url = reverse("users.confirm_access") + "?next=/msg/inbox/"
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
        response = self.client.post(confirm_url, {"password": "Qwerty123"})
        self.assertRedirect(response, failed_url)

        FailedLogin.objects.all().delete()

        # can once again submit incorrect passwords
        response = self.client.post(confirm_url, {"password": "nope"})
        self.assertFormError(response, "form", "password", "Password incorrect.")

        # and also correct ones
        response = self.client.post(confirm_url, {"password": "Qwerty123"})
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
            org.add_user(self.admin, OrgRole.ADMINISTRATOR)

        response = self.client.get(reverse("orgs.user_list"))
        self.assertEqual(200, response.status_code)

        response = self.client.post(reverse("orgs.user_delete", args=(self.editor.pk,)), {"delete": True})
        self.assertEqual(reverse("orgs.user_list"), response["Temba-Success"])

        self.editor.refresh_from_db()
        self.assertFalse(self.editor.is_active)

    def test_release_cross_brand(self):
        # create a second org
        branded_org = Org.objects.create(
            name="Other Brand Org",
            timezone=pytz.timezone("Africa/Kigali"),
            brand="some-other-brand",
            created_by=self.admin,
            modified_by=self.admin,
        )

        branded_org.add_user(self.admin, OrgRole.ADMINISTRATOR)

        # now release our user on our primary brand
        self.admin.release(self.customer_support, brand=settings.DEFAULT_BRAND)

        # our admin should still be good
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertEqual("admin@nyaruka.com", self.admin.email)

        # but she should be removed from org
        self.assertFalse(self.admin.get_orgs(brand="rapidpro").exists())

        # now lets release her from the branded org
        self.admin.release(self.customer_support, brand="some-other-brand")

        # now she gets deactivated and ambiguated and belongs to no orgs
        self.assertFalse(self.admin.is_active)
        self.assertNotEqual("admin@nyaruka.com", self.admin.email)
        self.assertFalse(self.admin.get_orgs().exists())

    def test_brand_aliases(self):
        # set our brand to our custom org
        self.org.brand = "custom"
        self.org.save(update_fields=("brand",))

        # create a second org on the .org version
        branded_org = Org.objects.create(
            name="Other Brand Org",
            timezone=pytz.timezone("Africa/Kigali"),
            brand="custom",
            created_by=self.admin,
            modified_by=self.admin,
        )
        branded_org.add_user(self.admin, OrgRole.ADMINISTRATOR)
        self.org2.add_user(self.admin, OrgRole.ADMINISTRATOR)

        # log in as admin
        self.login(self.admin)

        # check our choose page
        response = self.client.get(reverse("orgs.org_choose"), SERVER_NAME="custom-brand.org")
        self.assertEqual("custom", response.context["request"].branding["slug"])

        # should contain both orgs
        self.assertContains(response, "Other Brand Org")
        self.assertContains(response, "Nyaruka")
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
        self.surveyor.release(self.customer_support, brand=self.org.brand)
        self.editor.release(self.customer_support, brand=self.org.brand)
        self.user.release(self.customer_support, brand=self.org.brand)
        self.agent.release(self.customer_support, brand=self.org.brand)

        # still a user left, our org remains active
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_active)

        # now that we are the last user, we own it now
        self.assertEqual(1, len(self.admin.get_owned_orgs()))
        self.admin.release(self.customer_support, brand=self.org.brand)

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
            flow_languages=["eng"],
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
        self.child_org.add_user(self.user, OrgRole.ADMINISTRATOR)
        self.child_org.initialize()
        self.child_org.parent = self.parent_org
        self.child_org.save()

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

        FlowRun.objects.create(
            org=self.org,
            flow=child_flow,
            contact=child_contact,
            status=FlowRun.STATUS_COMPLETED,
            exited_on=timezone.now(),
        )

        # labels for our flows
        flow_label1 = FlowLabel.create(self.parent_org, self.admin, "Cool Flows")
        flow_label2 = FlowLabel.create(self.parent_org, self.admin, "Crazy Flows")
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
        parent_trigger.groups.add(self.parent_org.groups.all().first())

        FlowStart.objects.create(org=self.parent_org, flow=parent_flow)

        child_trigger = Trigger.create(
            self.child_org,
            flow=child_flow,
            trigger_type=Trigger.TYPE_KEYWORD,
            user=self.user,
            channel=self.child_channel,
            keyword="color",
        )
        child_trigger.groups.add(self.child_org.groups.all().first())

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
            self.parent_org,
            self.admin,
            start_date=date.today(),
            end_date=date.today(),
            flows=[parent_flow],
            with_fields=[parent_field],
            with_groups=(),
            responded_only=True,
            extra_urns=(),
        )
        ExportFinishedNotificationType.create(export)
        ExportFlowResultsTask.create(
            self.child_org,
            self.admin,
            start_date=date.today(),
            end_date=date.today(),
            flows=[child_flow],
            with_fields=[child_field],
            with_groups=(),
            responded_only=True,
            extra_urns=(),
        )

        export = ExportContactsTask.create(self.parent_org, self.admin, group=parent_group)
        ExportFinishedNotificationType.create(export)
        ExportContactsTask.create(self.child_org, self.admin, group=child_group)

        export = ExportMessagesTask.create(
            self.parent_org, self.admin, start_date=date.today(), end_date=date.today(), label=parent_label
        )
        ExportFinishedNotificationType.create(export)
        ExportMessagesTask.create(
            self.child_org,
            self.admin,
            start_date=date.today(),
            end_date=date.today(),
            label=child_label,
        )

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

    def release_org(self, org, child_org=None, delete=False, expected_files=3):

        with patch("temba.utils.s3.client", return_value=self.mock_s3):
            # save off the ids of our current users
            org_user_ids = list(org.users.values_list("id", flat=True))

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
            org.release(self.customer_support)
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
                self.assertFalse(org.msgs_labels.exists())

                # contacts, groups
                self.assertFalse(Contact.objects.filter(org=org).exists())
                self.assertFalse(ContactGroup.objects.filter(org=org).exists())

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
        self.release_org(self.child_org, delete=True, expected_files=2)

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
            delete_released_orgs()

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
    def test_create(self):
        new_org = Org.create(self.admin, brands.get_by_slug("rapidpro"), "Cool Stuff", pytz.timezone("Africa/Kigali"))
        self.assertEqual("Cool Stuff", new_org.name)
        self.assertEqual("rapidpro", new_org.brand)
        self.assertEqual(self.admin, new_org.created_by)
        self.assertEqual("en-us", new_org.language)
        self.assertEqual(["eng"], new_org.flow_languages)
        self.assertEqual("D", new_org.date_format)
        self.assertEqual(str(new_org.timezone), "Africa/Kigali")
        self.assertIn(self.admin, self.org.get_admins())

        # if timezone is US, should get MMDDYYYY dates
        new_org = Org.create(
            self.admin, brands.get_by_slug("rapidpro"), "Cool Stuff", pytz.timezone("America/Los_Angeles")
        )
        self.assertEqual("M", new_org.date_format)
        self.assertEqual(str(new_org.timezone), "America/Los_Angeles")

    def test_get_users(self):
        admin3 = self.create_user("bob@nyaruka.com")

        self.org.add_user(admin3, OrgRole.ADMINISTRATOR)
        self.org2.add_user(self.admin, OrgRole.ADMINISTRATOR)

        self.assertEqual(
            [self.admin, self.editor, admin3],
            list(self.org.get_users(roles=[OrgRole.ADMINISTRATOR, OrgRole.EDITOR]).order_by("id")),
        )
        self.assertEqual([self.user], list(self.org.get_users(roles=[OrgRole.VIEWER]).order_by("id")))
        self.assertEqual(
            [self.admin, self.admin2],
            list(self.org2.get_users(roles=[OrgRole.ADMINISTRATOR, OrgRole.EDITOR]).order_by("id")),
        )

        self.assertEqual(
            [self.admin, self.editor, self.agent, admin3],
            list(self.org.get_users(with_perm="tickets.ticket_assignee").order_by("id")),
        )
        self.assertEqual(
            [self.admin, self.admin2], list(self.org2.get_users(with_perm="tickets.ticket_assignee").order_by("id"))
        )

        self.assertEqual([self.admin, admin3], list(self.org.get_admins().order_by("id")))
        self.assertEqual([self.admin, self.admin2], list(self.org2.get_admins().order_by("id")))

    def test_get_owner(self):
        # admins take priority
        self.assertEqual(self.admin, self.org.get_owner())

        OrgMembership.objects.filter(org=self.org, role_code="A").delete()

        # then editors etc
        self.assertEqual(self.editor, self.org.get_owner())

        OrgMembership.objects.filter(org=self.org, role_code=OrgRole.EDITOR.code).delete()
        OrgMembership.objects.filter(org=self.org, role_code=OrgRole.VIEWER.code).delete()
        OrgMembership.objects.filter(org=self.org, role_code=OrgRole.AGENT.code).delete()
        OrgMembership.objects.filter(org=self.org, role_code=OrgRole.SURVEYOR.code).delete()

        # finally defaulting to org creator
        self.assertEqual(self.user, self.org.get_owner())

    def test_get_unique_slug(self):
        self.org.slug = "allo"
        self.org.save()

        self.assertEqual(Org.get_unique_slug("foo"), "foo")
        self.assertEqual(Org.get_unique_slug("Which part?"), "which-part")
        self.assertEqual(Org.get_unique_slug("Allo"), "allo-2")

    def test_set_flow_languages(self):
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

        # if location support is disabled in the settings, don't display country formax
        with override_settings(FEATURES={}):
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

        # no access if anonymous
        response = self.client.get(update_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # change the user language
        response = self.client.post(
            update_url,
            {
                "language": "pt-br",
                "first_name": "Admin",
                "last_name": "User",
                "email": "administrator@temba.com",
                "current_password": "Qwerty123",
            },
            HTTP_X_FORMAX=True,
        )
        self.assertEqual(200, response.status_code)

        # check that our user settings have changed
        del self.admin.settings  # clear cached_property
        self.assertEqual("pt-br", self.admin.settings.language)

    @patch("temba.flows.models.FlowStart.async_start")
    @mock_mailroom
    def test_org_flagging_and_suspending(self, mr_mocks, mock_async_start):
        self.login(self.admin)

        mark = self.create_contact("Mark", phone="+12065551212")
        flow = self.create_flow("Test")

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
        self.assertRaises(
            AssertionError,
            self.client.post,
            reverse("flows.flow_broadcast", args=[]),
            {"flow": flow.id, "query": f'uuid="{mark.uuid}"', "type": "contact"},
        )

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
        self.assertRaises(
            AssertionError,
            self.client.post,
            reverse("flows.flow_broadcast", args=[]),
            {"flow": flow.id, "query": f'uuid="{mark.uuid}"', "type": "contact"},
        )

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
            reverse("flows.flow_broadcast", args=[]),
            {"flow": flow.id, "query": f'uuid="{mark.uuid}"', "type": "contact"},
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
            brand="rapidpro",
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
        self.org.add_user(editor, OrgRole.EDITOR)
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
        home_url = reverse("orgs.org_home")

        # can't access as editor
        self.login(self.editor)
        response = self.client.get(url)
        self.assertLoginRedirect(response)

        # can't access as admin either because we don't have that feature enabled
        self.login(self.admin)
        response = self.client.get(url)
        self.assertRedirect(response, home_url)

        self.org.features = [Org.FEATURE_USERS]
        self.org.save(update_fields=("features",))

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
        APIToken.get_or_create(self.org, self.admin, role=OrgRole.SURVEYOR)
        APIToken.get_or_create(self.org, self.editor, role=OrgRole.SURVEYOR)

        actual_fields = response.context["form"].fields
        expected_fields = ["loc", "invite_emails", "invite_role"]
        for user in self.org.users.all():
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

        self.assertEqual({self.admin, self.agent, self.editor, self.user}, set(self.org.users.all()))
        self.assertEqual({self.admin}, set(self.org.get_users(roles=[OrgRole.ADMINISTRATOR])))
        self.assertEqual({self.user, self.editor}, set(self.org.get_users(roles=[OrgRole.EDITOR])))
        self.assertEqual(set(), set(self.org.get_users(roles=[OrgRole.VIEWER])))
        self.assertEqual(set(), set(self.org.get_users(roles=[OrgRole.SURVEYOR])))
        self.assertEqual({self.agent}, set(self.org.get_users(roles=[OrgRole.AGENT])))

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
        self.assertEqual(set(self.org.users.all()), {self.admin, self.agent, self.editor, self.user})
        self.assertEqual({self.admin}, set(self.org.get_users(roles=[OrgRole.ADMINISTRATOR])))
        self.assertEqual({self.user, self.editor}, set(self.org.get_users(roles=[OrgRole.EDITOR])))
        self.assertEqual(set(), set(self.org.get_users(roles=[OrgRole.VIEWER])))
        self.assertEqual(set(), set(self.org.get_users(roles=[OrgRole.SURVEYOR])))
        self.assertEqual({self.agent}, set(self.org.get_users(roles=[OrgRole.AGENT])))

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
        self.assertEqual(set(self.org.users.all()), {self.agent, self.editor, self.user})
        self.assertEqual({self.agent}, set(self.org.get_users(roles=[OrgRole.ADMINISTRATOR])))
        self.assertEqual({self.user}, set(self.org.get_users(roles=[OrgRole.EDITOR])))
        self.assertEqual(set(), set(self.org.get_users(roles=[OrgRole.VIEWER])))
        self.assertEqual({self.editor}, set(self.org.get_users(roles=[OrgRole.SURVEYOR])))
        self.assertEqual(set(), set(self.org.get_users(roles=[OrgRole.AGENT])))

        # editor will have lost their editor API token, but not their surveyor token
        self.editor.refresh_from_db()
        self.assertEqual([t.role.name for t in self.editor.api_tokens.filter(is_active=True)], ["Surveyors"])

        # and all our API tokens for the admin are deleted
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.api_tokens.filter(is_active=True).count(), 0)

        # make sure an existing user can not be invited again
        user = self.create_user("admin1@temba.com")
        self.org.add_user(user, OrgRole.ADMINISTRATOR)
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
        def create_invite(group, email):
            return Invitation.objects.create(
                org=self.org,
                user_group=group,
                email=email,
                created_by=self.admin,
                modified_by=self.admin,
            )

        editor_invitation = create_invite("E", "invitededitor@nyaruka.com")
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
        self.invited_editor = self.create_user("invitededitor@nyaruka.com")

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

        self.assertEqual(OrgRole.EDITOR, self.org.get_user_role(self.invited_editor))
        self.assertFalse(Invitation.objects.get(pk=editor_invitation.pk).is_active)

        # test it for each role
        for role in OrgRole:
            invite = create_invite(role.code, f"user.{role.code}@nyaruka.com")
            user = self.create_user(f"user.{role.code}@nyaruka.com")
            self.login(user)
            response = self.client.post(reverse("orgs.org_join_accept", args=[invite.secret]), follow=True)
            self.assertEqual(200, response.status_code)
            self.assertTrue(self.org.get_users(roles=[role]).filter(pk=user.pk).exists())

        # try an expired invite
        invite = create_invite("S", "invitedexpired@nyaruka.com")
        invite.is_active = False
        invite.save()
        expired_user = self.create_user("invitedexpired@nyaruka.com")
        self.login(expired_user)
        response = self.client.post(reverse("orgs.org_join_accept", args=[invite.secret]), follow=True)
        self.assertEqual(200, response.status_code)
        self.assertFalse(self.org.get_users(roles=[OrgRole.SURVEYOR]).filter(id=expired_user.id).exists())

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
        self.assertEqual(OrgRole.ADMINISTRATOR, self.org.get_user_role(new_invited_user))
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
        self.assertEqual(OrgRole.SURVEYOR, self.org.get_user_role(new_invited_user))

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

        # and that we have the surveyor role
        self.assertEqual(OrgRole.SURVEYOR, self.org.get_user_role(User.objects.get(username="beastmode@seahawks.com")))

    @patch("temba.orgs.views.Client", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_twilio_connect(self):
        with patch("temba.tests.twilio.MockTwilioClient.MockAccounts.get") as mock_get:
            mock_get.return_value = MockTwilioClient.MockAccount("Full")

            connect_url = reverse("orgs.org_twilio_connect")

            self.login(self.admin)

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
                    self.assertEqual(self.org.name, "Nyaruka")

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
        self.other_admin = self.create_user("other_admin@nyaruka.com")
        self.org.add_user(self.other_admin, OrgRole.ADMINISTRATOR)
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
        self.assertEqual(self.org.name, "Nyaruka")
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
        self.assertEqual(self.org.name, "Nyaruka")

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

    def test_org_get_limit(self):
        self.assertEqual(self.org.get_limit(Org.LIMIT_FIELDS), 250)
        self.assertEqual(self.org.get_limit(Org.LIMIT_GROUPS), 250)
        self.assertEqual(self.org.get_limit(Org.LIMIT_GLOBALS), 250)

        self.org.limits = dict(fields=500, groups=500)
        self.org.save()

        self.assertEqual(self.org.get_limit(Org.LIMIT_FIELDS), 500)
        self.assertEqual(self.org.get_limit(Org.LIMIT_GROUPS), 500)
        self.assertEqual(self.org.get_limit(Org.LIMIT_GLOBALS), 250)

    def test_org_api_rates(self):
        self.assertEqual(self.org.api_rates, {})

        self.org.api_rates = {"v2.contacts": "10000/hour"}
        self.org.save()

        self.assertEqual(self.org.api_rates, {"v2.contacts": "10000/hour"})

    def test_child_management(self):
        # error if an org without this feature tries to create a child
        with self.assertRaises(AssertionError):
            self.org.create_new(self.admin, "Sub Org", self.org.timezone, as_child=True)

        # enable feature and try again
        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        sub_org = self.org.create_new(self.admin, "Sub Org", self.org.timezone, as_child=True)

        # we should be linked to our parent with the same brand
        self.assertEqual(self.org, sub_org.parent)
        self.assertEqual(self.org.brand, sub_org.brand)
        self.assertEqual(self.admin, sub_org.created_by)

        # default values should be the same as parent
        self.assertEqual(self.org.timezone, sub_org.timezone)

        self.login(self.admin, choose_org=self.org)

        response = self.client.get(reverse("orgs.org_edit"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(len(response.context["sub_orgs"]), 1)

        # sub_org is deleted
        sub_org.release(self.customer_support)

        response = self.client.get(reverse("orgs.org_edit"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(len(response.context["sub_orgs"]), 0)

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
        Group.objects.get(name="Beta").user_set.add(self.admin)

        self.login(self.admin)

        deep_link = reverse("spa.level_2", args=["tickets", "all", "open"])
        response = self.client.get(deep_link)
        self.assertEqual(200, response.status_code)

    def assertMenu(self, url, count):
        response = self.assertListFetch(url, allow_viewers=True, allow_editors=True, allow_agents=True)
        menu = response.json()["results"]
        self.assertEqual(count, len(menu))

    def test_home(self):
        home_url = reverse("orgs.org_home")

        self.login(self.user)

        with self.assertNumQueries(13):
            response = self.client.get(home_url)

        # not so many options for viewers
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, len(response.context["formax"].sections))

        self.login(self.admin)

        with self.assertNumQueries(46):
            response = self.client.get(home_url)

        # more options for admins
        self.assertEqual(200, response.status_code)
        self.assertEqual(13, len(response.context["formax"].sections))

        # set a plan and add the users feature
        self.org.plan = "unicef"
        self.org.features = [Org.FEATURE_USERS]
        self.org.save(update_fields=("plan", "features"))

        response = self.client.get(home_url)
        self.assertEqual(15, len(response.context["formax"].sections))

    def test_menu(self):
        self.login(self.admin)
        self.assertMenu(reverse("orgs.org_menu"), 7)
        self.assertMenu(f"{reverse('orgs.org_menu')}settings/", 7)

        menu_url = reverse("orgs.org_menu")
        response = self.assertListFetch(menu_url, allow_viewers=True, allow_editors=True, allow_agents=True)
        menu = response.json()["results"]
        self.assertEqual(7, len(menu))

        # agents should only see tickets and settings
        self.login(self.agent)

        with self.assertNumQueries(9):
            response = self.client.get(menu_url)

        menu = response.json()["results"]
        self.assertEqual(2, len(menu))

        # customer support should only see the staff option
        self.login(self.customer_support)
        menu = self.client.get(menu_url).json()["results"]
        self.assertEqual(1, len(menu))
        self.assertEqual("Staff", menu[0]["name"])

        menu = self.client.get(f"{menu_url}staff/").json()["results"]
        self.assertEqual(2, len(menu))
        self.assertEqual("Workspaces", menu[0]["name"])
        self.assertEqual("Users", menu[1]["name"])

    def test_read(self):
        read_url = reverse("orgs.org_read", args=[self.org.id])

        # make our second org a child
        self.org2.parent = self.org
        self.org2.save()

        response = self.assertStaffOnly(read_url)

        # we should have a child in our context
        self.assertEqual(1, len(response.context["children"]))

        # we should have an option to flag
        self.assertContentMenu(read_url, self.customer_support, ["Edit", "Flag", "Verify", "Delete", "-", "Service"])

        # flag and content menu option should be inverted
        self.org.flag()
        response = self.client.get(read_url)
        self.assertContentMenu(read_url, self.customer_support, ["Edit", "Unflag", "Verify", "Delete", "-", "Service"])

        # no menu for inactive orgs
        self.org.is_active = False
        self.org.save()
        self.assertContentMenu(read_url, self.customer_support, [])

    def test_workspace(self):
        workspace_url = reverse("orgs.org_workspace")

        response = self.assertListFetch(workspace_url, allow_viewers=True, allow_editors=True, allow_agents=False)

        # make sure we have the appropriate number of sections
        self.assertEqual(7, len(response.context["formax"].sections))

        self.org.features = [Org.FEATURE_USERS, Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        response = self.assertListFetch(workspace_url, allow_viewers=True, allow_editors=True, allow_agents=False)

        # we now have workspace management
        self.assertEqual(8, len(response.context["formax"].sections))

        # create a child org
        self.child_org = Org.objects.create(
            name="Child Org",
            timezone=pytz.timezone("Africa/Kigali"),
            country=self.org.country,
            plan="parent",
            created_by=self.user,
            modified_by=self.user,
            parent=self.org,
        )

        with self.assertNumQueries(22):
            response = self.client.get(reverse("orgs.org_workspace"))

        # should have an extra menu option for our child (and section header)
        self.assertMenu(f"{reverse('orgs.org_menu')}settings/", 9)

    def test_org_grant(self):
        grant_url = reverse("orgs.org_grant")
        response = self.client.get(grant_url)
        self.assertRedirect(response, "/users/login/")

        self.user = self.create_user("tito@nyaruka.com")

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
        self.assertEqual(org.date_format, Org.DATE_FORMAT_DAY_FIRST)

        # check user exists and is admin
        self.assertEqual(OrgRole.ADMINISTRATOR, org.get_user_role(User.objects.get(username="john@carmack.com")))
        self.assertEqual(OrgRole.ADMINISTRATOR, org.get_user_role(User.objects.get(username="tito@nyaruka.com")))

        # try a new org with a user that already exists instead
        del post_data["password"]
        post_data["name"] = "id Software"

        response = self.client.post(grant_url, post_data, follow=True)

        self.assertContains(response, "created")

        org = Org.objects.get(name="id Software")
        self.assertEqual(org.date_format, Org.DATE_FORMAT_DAY_FIRST)

        self.assertEqual(OrgRole.ADMINISTRATOR, org.get_user_role(User.objects.get(username="john@carmack.com")))
        self.assertEqual(OrgRole.ADMINISTRATOR, org.get_user_role(User.objects.get(username="tito@nyaruka.com")))

        # try a new org with US timezone
        post_data["name"] = "Bulls"
        post_data["timezone"] = "America/Chicago"
        response = self.client.post(grant_url, post_data, follow=True)

        self.assertContains(response, "created")

        org = Org.objects.get(name="Bulls")
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

        self.login(self.admin)

        # user with email admin@nyaruka.com already exists and we set a password
        response = self.client.post(
            grant_url,
            {
                "email": "admin@nyaruka.com",
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
        self.user = self.create_user("tito@nyaruka.com")

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

        # should have a new org
        org = Org.objects.get(name="AlexCom")
        self.assertEqual(org.timezone, pytz.timezone("Africa/Kigali"))

        # of which our user is an administrator and can generate an API token
        self.assertIn(user, org.get_admins())
        self.assertIsNotNone(user.get_api_token(org))

        # not the logged in user at the signup time
        self.assertNotIn(self.user, org.get_admins())

    @override_settings(
        AUTH_PASSWORD_VALIDATORS=[
            {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
            {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
            {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
        ]
    )
    def test_signup(self):
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
        response = self.client.post(
            signup_url,
            {
                "first_name": "Eugene",
                "last_name": "Rwagasore",
                "email": "bad_email",
                "password": "badpass",
                "name": "Your Face",
                "timezone": "Africa/Kigali",
            },
        )
        self.assertFormError(response, "form", "email", "Enter a valid email address.")
        self.assertFormError(
            response, "form", "password", "This password is too short. It must contain at least 8 characters."
        )

        # submit with password that is too common
        response = self.client.post(
            signup_url,
            {
                "first_name": "Eugene",
                "last_name": "Rwagasore",
                "email": "eugene@temba.io",
                "password": "password",
                "name": "Your Face",
                "timezone": "Africa/Kigali",
            },
        )
        self.assertFormError(response, "form", "password", "This password is too common.")

        # submit with password that is all numerical
        response = self.client.post(
            signup_url,
            {
                "first_name": "Eugene",
                "last_name": "Rwagasore",
                "email": "eugene@temba.io",
                "password": "3464357358532",
                "name": "Your Face",
                "timezone": "Africa/Kigali",
            },
        )
        self.assertFormError(response, "form", "password", "This password is entirely numeric.")

        # submit with valid data (long email)
        response = self.client.post(
            signup_url,
            {
                "first_name": "Eugene",
                "last_name": "Rwagasore",
                "email": "myal12345678901234567890@relieves.org",
                "password": "HelloWorld1",
                "name": "Relieves World",
                "timezone": "Africa/Kigali",
            },
        )
        self.assertEqual(response.status_code, 302)

        # should have a new user
        user = User.objects.get(username="myal12345678901234567890@relieves.org")
        self.assertEqual(user.first_name, "Eugene")
        self.assertEqual(user.last_name, "Rwagasore")
        self.assertEqual(user.email, "myal12345678901234567890@relieves.org")
        self.assertTrue(user.check_password("HelloWorld1"))

        # should have a new org
        org = Org.objects.get(name="Relieves World")
        self.assertEqual(org.timezone, pytz.timezone("Africa/Kigali"))
        self.assertEqual(str(org), "Relieves World")

        # of which our user is an administrator, and can generate an API token
        self.assertIn(user, org.get_admins())
        self.assertIsNotNone(user.get_api_token(org))

        # check default org content was created correctly
        system_fields = set(org.fields.filter(is_system=True).values_list("key", flat=True))
        system_groups = set(org.groups.filter(is_system=True).values_list("name", flat=True))
        sample_flows = set(org.flows.values_list("name", flat=True))
        internal_ticketer = org.ticketers.get()

        self.assertEqual({"created_on", "id", "language", "last_seen_on", "name"}, system_fields)
        self.assertEqual({"Active", "Archived", "Blocked", "Stopped", "Open Tickets"}, system_groups)
        self.assertEqual(
            {"Sample Flow - Order Status Checker", "Sample Flow - Satisfaction Survey", "Sample Flow - Simple Poll"},
            sample_flows,
        )
        self.assertEqual("RapidPro Tickets", internal_ticketer.name)

        # should now be able to go to channels page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertEqual(200, response.status_code)

        # check that we have all the tabs
        self.assertContains(response, reverse("msgs.msg_inbox"))
        self.assertContains(response, reverse("flows.flow_list"))
        self.assertContains(response, reverse("contacts.contact_list"))
        self.assertContains(response, reverse("channels.channel_list"))
        self.assertContains(response, reverse("orgs.org_home"))

        # can't signup again with same email
        response = self.client.post(
            signup_url,
            {
                "first_name": "Eugene",
                "last_name": "Rwagasore",
                "email": "myal12345678901234567890@relieves.org",
                "password": "HelloWorld1",
                "name": "Relieves World 2",
                "timezone": "Africa/Kigali",
            },
        )
        self.assertFormError(response, "form", "email", "That email address is already used")

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

    def test_create_new(self):
        home_url = reverse("orgs.org_home")
        create_url = reverse("orgs.org_create")

        self.login(self.admin)

        # by default orgs don't have this feature
        response = self.client.get(home_url)

        self.assertNotContains(response, ">New Workspace</a>")

        # trying to access the modal directly should redirect
        response = self.client.get(create_url)
        self.assertRedirect(response, "/org/home/")

        self.org.features = [Org.FEATURE_NEW_ORGS]
        self.org.save(update_fields=("features",))

        response = self.client.get(home_url)
        self.assertContains(response, ">New Workspace</a>")

        # give org2 the same feature
        self.org2.features = [Org.FEATURE_CHILD_ORGS]
        self.org2.save(update_fields=("features",))

        # since we can only create new orgs, we don't show type as an option
        self.assertCreateFetch(create_url, allow_viewers=False, allow_editors=False, form_fields=["name", "timezone"])

        # try to submit an empty form
        response = self.assertCreateSubmit(
            create_url, {}, form_errors={"name": "This field is required.", "timezone": "This field is required."}
        )

        # submit with valid values to create a new org...
        response = self.assertCreateSubmit(
            create_url,
            {"name": "My Other Org", "timezone": "Africa/Nairobi"},
            new_obj_query=Org.objects.filter(name="My Other Org", parent=None),
        )

        new_org = Org.objects.get(name="My Other Org")
        self.assertEqual([], new_org.features)
        self.assertEqual("Africa/Nairobi", str(new_org.timezone))
        self.assertEqual(OrgRole.ADMINISTRATOR, new_org.get_user_role(self.admin))

        # should be now logged into that org
        self.assertRedirect(response, "/msg/inbox/")
        response = self.client.get("/msg/inbox/")
        self.assertEqual(str(new_org.id), response.headers["X-Temba-Org"])

    def test_create_child(self):
        home_url = reverse("orgs.org_home")
        create_url = reverse("orgs.org_create")

        self.login(self.admin)

        # by default orgs don't have the new_orgs or child_orgs feature
        response = self.client.get(home_url)

        self.assertNotContains(response, ">New Workspace</a>")

        # trying to access the modal directly should redirect
        response = self.client.get(create_url)
        self.assertRedirect(response, "/org/home/")

        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        response = self.client.get(home_url)
        self.assertContains(response, ">New Workspace</a>")

        # give org2 the same feature
        self.org2.features = [Org.FEATURE_CHILD_ORGS]
        self.org2.save(update_fields=("features",))

        # since we can only create child orgs, we don't show type as an option
        self.assertCreateFetch(create_url, allow_viewers=False, allow_editors=False, form_fields=["name", "timezone"])

        # try to submit an empty form
        response = self.assertCreateSubmit(
            create_url, {}, form_errors={"name": "This field is required.", "timezone": "This field is required."}
        )

        # submit with valid values to create a child org...
        response = self.assertCreateSubmit(
            create_url,
            {"name": "My Child Org", "timezone": "Africa/Nairobi"},
            new_obj_query=Org.objects.filter(name="My Child Org", parent=self.org),
        )

        child_org = Org.objects.get(name="My Child Org")
        self.assertEqual([], child_org.features)
        self.assertEqual("Africa/Nairobi", str(child_org.timezone))
        self.assertEqual(OrgRole.ADMINISTRATOR, child_org.get_user_role(self.admin))

        # should have been redirected to child management page
        self.assertRedirect(response, "/org/sub_orgs/")

    def test_create_child_or_new(self):
        create_url = reverse("orgs.org_create")

        self.login(self.admin)

        self.org.features = [Org.FEATURE_NEW_ORGS, Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        # give org2 the same feature
        self.org2.features = [Org.FEATURE_NEW_ORGS, Org.FEATURE_CHILD_ORGS]
        self.org2.save(update_fields=("features",))

        # because we can create both new orgs and child orgs, type is an option
        self.assertCreateFetch(
            create_url,
            allow_viewers=False,
            allow_editors=False,
            form_fields=["type", "name", "timezone"],
        )

        # create new org
        self.assertCreateSubmit(
            create_url,
            {"type": "new", "name": "New Org", "timezone": "Africa/Nairobi"},
            new_obj_query=Org.objects.filter(name="New Org", parent=None),
        )

        # create child org
        self.assertCreateSubmit(
            create_url,
            {"type": "child", "name": "Child Org", "timezone": "Africa/Nairobi"},
            new_obj_query=Org.objects.filter(name="Child Org", parent=self.org),
        )

    def test_create_child_spa(self):
        create_url = reverse("orgs.org_create")

        self.login(self.admin)

        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        response = self.client.post(create_url, {"name": "Child Org", "timezone": "Africa/Nairobi"}, HTTP_TEMBA_SPA=1)

        self.assertRedirect(response, "/org/manage_accounts_sub_org/")

    def test_child_management(self):
        sub_orgs_url = reverse("orgs.org_sub_orgs")
        home_url = reverse("orgs.org_home")

        self.login(self.admin)

        # we don't see button if we don't have child orgs
        response = self.client.get(home_url)
        self.assertNotContains(response, "Manage Workspaces")

        # enable child orgs and create some child orgs
        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))
        child1 = self.org.create_new(self.admin, "Child Org 1", self.org.timezone, as_child=True)
        child2 = self.org.create_new(self.admin, "Child Org 2", self.org.timezone, as_child=True)

        # now we see the button and can view that page
        self.login(self.admin, choose_org=self.org)
        response = self.client.get(home_url)
        self.assertContains(response, "Manage Workspaces")

        response = self.assertListFetch(
            sub_orgs_url, allow_viewers=False, allow_editors=False, context_objects=[self.org, child1, child2]
        )

        child1_edit_url = reverse("orgs.org_edit_sub_org") + f"?org={child1.id}"
        child1_accounts_url = reverse("orgs.org_manage_accounts_sub_org") + f"?org={child1.id}"

        self.assertContains(response, child1_edit_url)
        self.assertContains(response, child1_accounts_url)

        # we can also access the manage accounts page
        response = self.client.get(child1_accounts_url)
        self.assertEqual(200, response.status_code)

        response = self.client.get(child1_accounts_url, HTTP_TEMBA_SPA=1)
        self.assertContains(response, "Edit Workspace")

        # edit our sub org's details
        response = self.client.post(
            child1_edit_url,
            {"name": "New Child Name", "timezone": "Africa/Nairobi", "date_format": "Y", "language": "es"},
        )
        self.assertEqual(sub_orgs_url, response.url)

        child1.refresh_from_db()
        self.assertEqual("New Child Name", child1.name)
        self.assertEqual("/org/sub_orgs/", response.url)

        # edit our sub org's details in a spa view
        response = self.client.post(
            child1_edit_url,
            {"name": "Spa Child Name", "timezone": "Africa/Nairobi", "date_format": "Y", "language": "es"},
            HTTP_TEMBA_SPA=1,
        )

        self.assertEqual(child1_accounts_url, response.url)

        child1.refresh_from_db()
        self.assertEqual("Spa Child Name", child1.name)
        self.assertEqual("Africa/Nairobi", str(child1.timezone))
        self.assertEqual("Y", child1.date_format)
        self.assertEqual("es", child1.language)

        # if org doesn't exist, 404
        response = self.client.get(f"{reverse('orgs.org_edit_sub_org')}?org=3464374")
        self.assertEqual(404, response.status_code)

        self.login(self.admin2)

        # same if it's not a child of the request org
        response = self.client.get(f"{reverse('orgs.org_edit_sub_org')}?org={child1.id}")
        self.assertEqual(404, response.status_code)

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
        org3.add_user(self.editor, OrgRole.EDITOR)

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

        # unless they are staff
        self.assertRedirect(self.requestView(choose_url, self.customer_support), "/org/manage/")

        # turn editor into a multi-org user
        self.org2.add_user(self.editor, OrgRole.EDITOR)

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

    def test_delete(self):
        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        workspace = self.org.create_new(self.admin, "Child Workspace", self.org.timezone, as_child=True)
        delete_workspace = reverse("orgs.org_delete", args=[workspace.id])

        # choose the parent org, try to delete the workspace
        self.assertDeleteFetch(delete_workspace)

        # schedule for deletion
        response = self.client.get(delete_workspace)
        self.assertContains(response, "You are about to delete the workspace <b>Child Workspace</b>")

        # go through with it, redirects to main workspace page
        response = self.client.post(delete_workspace)
        self.assertEqual(reverse("orgs.org_workspace"), response["Temba-Success"])

        workspace.refresh_from_db()
        self.assertFalse(workspace.is_active)

        # can't delete primary workspace
        primary_delete = reverse("orgs.org_delete", args=[self.org.id])
        response = self.client.get(primary_delete)
        self.assertRedirect(response, "/users/login/")

        response = self.client.post(primary_delete)
        self.assertRedirect(response, "/users/login/")

        self.login(self.customer_support)
        primary_delete = reverse("orgs.org_delete", args=[self.org.id])
        response = self.client.get(primary_delete)
        self.assertContains(response, "You are about to delete the workspace <b>Nyaruka</b>")

        response = self.client.post(primary_delete)
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)

    def test_administration(self):
        self.setUpLocations()

        manage_url = reverse("orgs.org_manage")
        update_url = reverse("orgs.org_update", args=[self.org.id])

        self.assertStaffOnly(manage_url)
        self.assertStaffOnly(update_url)

        def assertOrgFilter(query: str, expected_orgs: list):
            response = self.client.get(manage_url + query)
            self.assertEqual(expected_orgs, list(response.context["object_list"]))

        assertOrgFilter("", [self.org2, self.org])
        assertOrgFilter("?filter=all", [self.org2, self.org])
        assertOrgFilter("?filter=xxxx", [self.org2, self.org])
        assertOrgFilter("?filter=flagged", [])
        assertOrgFilter("?filter=anon", [])
        assertOrgFilter("?filter=suspended", [])
        assertOrgFilter("?filter=verified", [])

        self.org.flag()

        assertOrgFilter("?filter=flagged", [self.org])

        self.org2.verify()

        assertOrgFilter("?filter=verified", [self.org2])

        # and can go to our org
        response = self.client.get(update_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            [
                "name",
                "features",
                "is_anon",
                "is_suspended",
                "is_flagged",
                "channels_limit",
                "fields_limit",
                "globals_limit",
                "groups_limit",
                "labels_limit",
                "teams_limit",
                "topics_limit",
                "loc",
            ],
            list(response.context["form"].fields.keys()),
        )

        # make some changes to our org
        response = self.client.post(
            update_url,
            {
                "name": "Temba II",
                "features": ["new_orgs"],
                "is_anon": False,
                "is_suspended": False,
                "is_flagged": False,
                "channels_limit": 20,
                "fields_limit": 300,
                "globals_limit": "",
                "groups_limit": 400,
                "labels_limit": "",
                "teams_limit": "",
                "topics_limit": "",
            },
        )
        self.assertEqual(302, response.status_code)

        self.org.refresh_from_db()
        self.assertEqual("Temba II", self.org.name)
        self.assertEqual(["new_orgs"], self.org.features)
        self.assertEqual(self.org.get_limit(Org.LIMIT_FIELDS), 300)
        self.assertEqual(self.org.get_limit(Org.LIMIT_GLOBALS), 250)  # uses default
        self.assertEqual(self.org.get_limit(Org.LIMIT_GROUPS), 400)
        self.assertEqual(self.org.get_limit(Org.LIMIT_CHANNELS), 20)

        # unflag org
        self.client.post(update_url, {"action": "unflag"})
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_flagged)

        # verify
        self.client.post(update_url, {"action": "verify"})
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_verified())

        # flag org
        self.client.post(update_url, {"action": "flag"})
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_flagged)

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

        response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "Qwerty123"}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("msgs.msg_inbox"))

        response = self.client.post(login_url, {"username": "ADMIN@nyaruka.com", "password": "Qwerty123"}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("msgs.msg_inbox"))

        # passwords stay case sensitive
        response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "QWERTY123"}, follow=True)
        self.assertIn("form", response.context)
        self.assertTrue(response.context["form"].errors)

    @mock_mailroom
    def test_service(self, mr_mocks):
        service_url = reverse("orgs.org_service")

        # without logging in, try to service our main org
        response = self.client.post(service_url, dict(organization=self.org.id))
        self.assertLoginRedirect(response)

        # try logging in with a normal user
        self.login(self.admin)

        # same thing, no permission
        response = self.client.post(service_url, dict(organization=self.org.id))
        self.assertLoginRedirect(response)

        # ok, log in as our cs rep
        self.login(self.customer_support)

        # invalid org just redirects back to manage page
        response = self.client.post(service_url, dict(organization=325253256))
        self.assertRedirect(response, "/org/manage/")

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
        self.assertEqual(self.customer_support, contact.created_by)

    def test_languages(self):
        home_url = reverse("orgs.org_home")
        langs_url = reverse("orgs.org_languages")

        self.org.set_flow_languages(self.admin, ["eng"])

        response = self.requestView(home_url, self.admin)
        self.assertEqual("English", response.context["primary_lang"])
        self.assertEqual([], response.context["other_langs"])

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


class UserCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_list(self):
        list_url = reverse("orgs.user_list")

        self.assertStaffOnly(list_url)

        response = self.requestView(list_url, self.customer_support)
        self.assertEqual(9, len(response.context["object_list"]))

        response = self.requestView(list_url + "?filter=beta", self.customer_support)
        self.assertEqual(set(), set(response.context["object_list"]))

        response = self.requestView(list_url + "?filter=staff", self.customer_support)
        self.assertEqual({self.customer_support, self.superuser}, set(response.context["object_list"]))

        response = self.requestView(list_url + "?search=admin@nyaruka.com", self.customer_support)
        self.assertEqual({self.admin}, set(response.context["object_list"]))

        response = self.requestView(list_url + "?search=admin@nyaruka.com", self.customer_support)
        self.assertEqual({self.admin}, set(response.context["object_list"]))

        response = self.requestView(list_url + "?search=Andy", self.customer_support)
        self.assertEqual({self.admin}, set(response.context["object_list"]))

    def test_read(self):
        read_url = reverse("orgs.user_read", args=[self.editor.id])

        # this is a customer support only view
        self.assertStaffOnly(read_url)

        response = self.requestView(read_url, self.customer_support)
        self.assertEqual(200, response.status_code)

    def test_update(self):
        update_url = reverse("orgs.user_update", args=[self.editor.id])

        # this is a customer support only view
        self.assertStaffOnly(update_url)

        response = self.requestView(update_url, self.customer_support)
        self.assertEqual(200, response.status_code)

        alphas = Group.objects.get(name="Alpha")
        betas = Group.objects.get(name="Beta")
        current_password = self.editor.password

        # submit without new password
        response = self.requestView(
            update_url,
            self.customer_support,
            post_data={
                "email": "eddy@nyaruka.com",
                "first_name": "Edward",
                "last_name": "",
                "groups": [alphas.id, betas.id],
            },
        )
        self.assertEqual(302, response.status_code)

        self.editor.refresh_from_db()
        self.assertEqual("eddy@nyaruka.com", self.editor.email)
        self.assertEqual("eddy@nyaruka.com", self.editor.username)  # should match email
        self.assertEqual(current_password, self.editor.password)
        self.assertEqual("Edward", self.editor.first_name)
        self.assertEqual("", self.editor.last_name)
        self.assertEqual({alphas, betas}, set(self.editor.groups.all()))

        # submit with new password and one less group
        response = self.requestView(
            update_url,
            self.customer_support,
            post_data={
                "email": "eddy@nyaruka.com",
                "new_password": "Asdf1234",
                "first_name": "Edward",
                "last_name": "",
                "groups": [alphas.id],
            },
        )
        self.assertEqual(302, response.status_code)

        self.editor.refresh_from_db()
        self.assertEqual("eddy@nyaruka.com", self.editor.email)
        self.assertEqual("eddy@nyaruka.com", self.editor.username)
        self.assertNotEqual(current_password, self.editor.password)
        self.assertEqual("Edward", self.editor.first_name)
        self.assertEqual("", self.editor.last_name)
        self.assertEqual({alphas}, set(self.editor.groups.all()))

    def test_delete(self):
        delete_url = reverse("orgs.user_delete", args=[self.editor.id])

        # this is a customer support only view
        self.assertStaffOnly(delete_url)

        response = self.requestView(delete_url, self.customer_support)
        self.assertEqual(200, response.status_code)
        self.assertNotContains(response, "Nyaruka")  # editor doesn't own this org

        # make editor the owner of the org
        OrgMembership.objects.filter(org=self.org, role_code=OrgRole.ADMINISTRATOR.code).delete()
        OrgMembership.objects.filter(org=self.org, role_code=OrgRole.VIEWER.code).delete()
        OrgMembership.objects.filter(org=self.org, role_code=OrgRole.AGENT.code).delete()
        OrgMembership.objects.filter(org=self.org, role_code=OrgRole.SURVEYOR.code).delete()

        response = self.requestView(delete_url, self.customer_support)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Nyaruka")

        response = self.requestView(delete_url, self.customer_support, post_data={})
        self.assertEqual(reverse("orgs.user_list"), response["Temba-Success"])

        self.editor.refresh_from_db()
        self.assertFalse(self.editor.is_active)

        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)


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
        # import file has invalid expires for an IVR flow so it should get the default (5)
        self.get_flow("ivr")

        self.assertEqual(Flow.objects.filter(flow_type=Flow.TYPE_VOICE).count(), 1)
        voice_flow = Flow.objects.get(flow_type=Flow.TYPE_VOICE)
        self.assertEqual(voice_flow.name, "IVR Flow")
        self.assertEqual(voice_flow.expires_after_minutes, 5)

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

        group = ContactGroup.objects.get(name="Survey Audience")

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

        age = ContactField.user_fields.get(key="age", name="Age")  # created from expression reference
        gender = ContactField.user_fields.get(key="gender")  # created from action reference

        farmers = ContactGroup.objects.get(name="Farmers")
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
            self.org, self.admin, "New Child 2", Flow.TYPE_MESSAGE, uuid="a925453e-ad31-46bd-858a-e01136732181"
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
                ContactGroup.objects.filter(uuid=dep["uuid"]).exists(),
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

        # we should have 5 non-system groups (all manual since we can only create manual groups from group references)
        self.assertEqual(ContactGroup.objects.filter(is_system=False).count(), 5)
        self.assertEqual(ContactGroup.objects.filter(is_system=False, group_type="M").count(), 5)

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

        # we should have 5 non-system groups (2 query based)
        self.assertEqual(ContactGroup.objects.filter(is_system=False).count(), 5)
        self.assertEqual(ContactGroup.objects.filter(is_system=False, group_type="M").count(), 3)
        self.assertEqual(ContactGroup.objects.filter(is_system=False, group_type="Q").count(), 2)

        # new fields should have been created for the dynamic groups
        likes_cats = ContactField.user_fields.get(key="likes_cats")
        facts_per_day = ContactField.user_fields.get(key="facts_per_day")

        # but without implicit fields in the export, the details aren't correct
        self.assertEqual(likes_cats.name, "Likes Cats")
        self.assertEqual(likes_cats.value_type, "T")
        self.assertEqual(facts_per_day.name, "Facts Per Day")
        self.assertEqual(facts_per_day.value_type, "T")

        cat_fanciers = ContactGroup.objects.get(name="Cat Fanciers")
        self.assertEqual(cat_fanciers.query, 'likes_cats = "true"')
        self.assertEqual(set(cat_fanciers.query_fields.all()), {likes_cats})

        cat_blasts = ContactGroup.objects.get(name="Cat Blasts")
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

        # we should have 5 non-system groups (2 query based)
        self.assertEqual(ContactGroup.objects.filter(is_system=False).count(), 5)
        self.assertEqual(ContactGroup.objects.filter(is_system=False, group_type="M").count(), 3)
        self.assertEqual(ContactGroup.objects.filter(is_system=False, group_type="Q").count(), 2)

        # new fields should have been created for the dynamic groups
        likes_cats = ContactField.user_fields.get(key="likes_cats")
        facts_per_day = ContactField.user_fields.get(key="facts_per_day")

        # and with implicit fields in the export, the details should be correct
        self.assertEqual(likes_cats.name, "Really Likes Cats")
        self.assertEqual(likes_cats.value_type, "T")
        self.assertEqual(facts_per_day.name, "Facts-Per-Day")
        self.assertEqual(facts_per_day.value_type, "N")

        cat_fanciers = ContactGroup.objects.get(name="Cat Fanciers")
        self.assertEqual(cat_fanciers.query, 'likes_cats = "true"')
        self.assertEqual(set(cat_fanciers.query_fields.all()), {likes_cats})

        cat_blasts = ContactGroup.objects.get(name="Cat Blasts")
        self.assertEqual(cat_blasts.query, "facts_per_day = 1")
        self.assertEqual(set(cat_blasts.query_fields.all()), {facts_per_day})

    def test_import_flow_with_triggers(self):
        flow1 = self.create_flow("Test 1")
        flow2 = self.create_flow("Test 2")

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
            self.assertEqual(3, ContactGroup.objects.filter(org=self.org, is_system=False).count())
            self.assertEqual(1, Label.objects.filter(org=self.org).count())
            self.assertEqual(
                1, ContactField.user_fields.filter(org=self.org, value_type="D", name="Next Appointment").count()
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
        message_flow.update_single_message_flow(self.admin, {"eng": "No reminders for you!"}, base_language="eng")

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

        group = ContactGroup.objects.get(name="Pending Appointments")
        group.name = "A new group"
        group.save(update_fields=("name",))

        # it should fall back on UUIDs and not create new objects even though the names changed
        self.org.import_app(exported, self.admin, site="http://app.rapidpro.io")

        assert_object_counts()

        # and our objects should have the same names as before
        self.assertEqual("Confirm Appointment", Flow.objects.get(pk=flow.pk).name)
        self.assertEqual("Appointment Schedule", Campaign.objects.filter(is_active=True).first().name)

        # except the group.. we don't mess with their names
        self.assertFalse(ContactGroup.objects.filter(name="Pending Appointments").exists())
        self.assertTrue(ContactGroup.objects.filter(name="A new group").exists())

        # let's rename our objects again
        flow.name = "A new name"
        flow.save(update_fields=("name",))

        campaign.name = "A new campaign"
        campaign.save(update_fields=("name",))

        group.name = "A new group"
        group.save(update_fields=("name",))

        # now import the same import but pretend it's from a different site
        self.org.import_app(exported, self.admin, site="http://temba.io")

        # the newly named objects won't get updated in this case and we'll create new ones instead
        self.assertEqual(
            9, Flow.objects.filter(org=self.org, is_archived=False, flow_type="M", is_system=False).count()
        )
        self.assertEqual(2, Campaign.objects.filter(org=self.org, is_archived=False).count())
        self.assertEqual(4, ContactGroup.objects.filter(org=self.org, is_system=False).count())

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


class DefaultFlowLanguagesTest(MigrationTest):
    app = "orgs"
    migrate_from = "0115_alter_org_plan"
    migrate_to = "0116_default_flow_languages"

    def setUpBeforeMigration(self, apps):
        self.org3 = Org.objects.create(
            name="Foo",
            timezone="Africa/Kigali",
            brand="rapidpro",
            created_by=self.admin,
            modified_by=self.admin,
            flow_languages=[],
        )
        self.org4 = Org.objects.create(
            name="Foo",
            timezone="Africa/Kigali",
            brand="rapidpro",
            created_by=self.admin,
            modified_by=self.admin,
            flow_languages=["kin"],
        )

    def test_migration(self):
        self.org3.refresh_from_db()
        self.org4.refresh_from_db()

        self.assertEqual(["eng"], self.org3.flow_languages)
        self.assertEqual(["kin"], self.org4.flow_languages)  # unchanged
