import io
import smtplib
from datetime import date, datetime, timedelta, timezone as tzone
from unittest.mock import patch
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django_redis import get_redis_connection
from smartmin.users.models import FailedLogin, RecoveryToken

from django.conf import settings
from django.contrib.auth.models import Group
from django.core import mail
from django.core.files.storage import default_storage
from django.db.models import Model
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba import mailroom
from temba.api.models import APIToken, Resthook, WebHookEvent
from temba.archives.models import Archive
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import Channel, ChannelLog, SyncEvent
from temba.classifiers.models import Classifier
from temba.classifiers.types.wit import WitType
from temba.contacts.models import (
    URN,
    Contact,
    ContactExport,
    ContactField,
    ContactGroup,
    ContactImport,
    ContactImportBatch,
)
from temba.flows.models import Flow, FlowLabel, FlowRun, FlowSession, FlowStart, FlowStartCount, ResultsExport
from temba.globals.models import Global
from temba.locations.models import AdminBoundary
from temba.msgs.models import Label, MessageExport, Msg
from temba.notifications.incidents.builtin import ChannelDisconnectedIncidentType
from temba.notifications.types.builtin import ExportFinishedNotificationType
from temba.request_logs.models import HTTPLog
from temba.schedules.models import Schedule
from temba.templates.models import TemplateTranslation
from temba.tests import CRUDLTestMixin, TembaTest, matchers, mock_mailroom
from temba.tests.base import get_contact_search
from temba.tests.s3 import MockS3Client, jsonlgz_encode
from temba.tickets.models import TicketExport
from temba.triggers.models import Trigger
from temba.utils import json, languages
from temba.utils.uuid import uuid4
from temba.utils.views import TEMBA_MENU_SELECTION

from .context_processors import RolePermsWrapper
from .models import (
    BackupToken,
    DefinitionExport,
    Export,
    Invitation,
    Org,
    OrgImport,
    OrgMembership,
    OrgRole,
    User,
    UserSettings,
)
from .tasks import (
    delete_released_orgs,
    expire_invitations,
    restart_stalled_exports,
    send_user_verification_email,
    trim_exports,
)


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
        self.assertTrue(perms["orgs"]["org_delete_child"])

        perms = RolePermsWrapper(OrgRole.EDITOR)

        self.assertTrue(perms["msgs"]["msg_list"])
        self.assertTrue(perms["contacts"]["contact_update"])
        self.assertFalse(perms["orgs"]["org_manage_accounts"])
        self.assertFalse(perms["orgs"]["org_delete_child"])

        perms = RolePermsWrapper(OrgRole.VIEWER)

        self.assertTrue(perms["msgs"]["msg_list"])
        self.assertFalse(perms["contacts"]["contact_update"])
        self.assertFalse(perms["orgs"]["org_manage_accounts"])
        self.assertFalse(perms["orgs"]["org_delete_child"])

        self.assertFalse(perms["msgs"]["foo"])  # no blow up if perm doesn't exist
        self.assertFalse(perms["chickens"]["foo"])  # or app doesn't exist

        with self.assertRaises(TypeError):
            list(perms)


class InvitationTest(TembaTest):
    def test_model(self):
        invitation = Invitation.objects.create(
            org=self.org,
            user_group="E",
            email="invitededitor@nyaruka.com",
            created_by=self.admin,
            modified_by=self.admin,
        )

        self.assertEqual(OrgRole.EDITOR, invitation.role)

        invitation.send()

        self.assertEqual(1, len(mail.outbox))
        self.assertEqual(["invitededitor@nyaruka.com"], mail.outbox[0].recipients())
        self.assertEqual("RapidPro Invitation", mail.outbox[0].subject)
        self.assertIn(f"https://app.rapidpro.io/org/join/{invitation.secret}/", mail.outbox[0].body)

        invitation.release()

        self.assertFalse(invitation.is_active)

    def test_expire_task(self):
        invitation1 = Invitation.objects.create(
            org=self.org,
            user_group="E",
            email="neweditor@nyaruka.com",
            created_by=self.admin,
            created_on=timezone.now() - timedelta(days=31),
            modified_by=self.admin,
        )
        invitation2 = Invitation.objects.create(
            org=self.org,
            user_group="T",
            email="newagent@nyaruka.com",
            created_by=self.admin,
            created_on=timezone.now() - timedelta(days=29),
            modified_by=self.admin,
        )

        expire_invitations()

        invitation1.refresh_from_db()
        invitation2.refresh_from_db()

        self.assertFalse(invitation1.is_active)
        self.assertTrue(invitation2.is_active)


class UserTest(TembaTest):
    def test_model(self):
        user = User.create("jim@rapidpro.io", "Jim", "McFlow", password="super")

        self.assertTrue(UserSettings.objects.filter(user=user).exists())  # created by signal

        with self.assertNumQueries(0):
            self.assertIsNone(user.settings.last_auth_on)

        # reload the user instance - now accessing settings should lazily trigger a query
        user = User.objects.get(id=user.id)
        with self.assertNumQueries(1):
            self.assertIsNone(user.settings.last_auth_on)
        with self.assertNumQueries(0):
            self.assertIsNone(user.settings.last_auth_on)

        # unless we prefetch
        user = User.objects.select_related("settings").get(id=user.id)
        with self.assertNumQueries(0):
            self.assertIsNone(user.settings.last_auth_on)

        self.org.add_user(user, OrgRole.EDITOR)
        self.org2.add_user(user, OrgRole.AGENT)

        self.assertEqual("Jim McFlow", user.name)
        self.assertFalse(user.is_alpha)
        self.assertFalse(user.is_beta)
        self.assertEqual({"email": "jim@rapidpro.io", "name": "Jim McFlow"}, user.as_engine_ref())
        self.assertEqual([self.org, self.org2], list(user.get_orgs().order_by("id")))
        self.assertEqual(40, len(user.get_api_token(self.org)))
        self.assertIsNone(user.get_api_token(self.org2))  # can't generate API token as agent

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
        for org, perm, checks in tests:
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
        self.assertTrue(response.url.endswith("?next=/msg/"))

        # view login page
        response = self.client.get(login_url)
        self.assertEqual(200, response.status_code)

        # submit incorrect username and password
        response = self.client.post(login_url, {"username": "jim", "password": "pass123"})
        self.assertEqual(200, response.status_code)
        self.assertFormError(
            response.context["form"],
            None,
            "Please enter a correct username and password. Note that both fields may be case-sensitive.",
        )

        # submit correct username and password
        response = self.client.post(login_url, {"username": "admin@nyaruka.com", "password": "Qwerty123"})
        self.assertRedirect(response, reverse("orgs.org_choose"))

        self.admin.settings.refresh_from_db()
        self.assertIsNotNone(self.admin.settings.last_auth_on)

        # logout and enable 2FA
        self.client.logout()
        self.admin.enable_2fa()

        # can't access two-factor verify page yet
        response = self.client.get(verify_url)
        self.assertLoginRedirect(response)

        # login via login page again
        response = self.client.post(
            login_url + "?next=/msg/", {"username": "admin@nyaruka.com", "password": "Qwerty123"}
        )
        self.assertRedirect(response, verify_url)
        self.assertTrue(response.url.endswith("?next=/msg/"))

        # view two-factor verify page
        response = self.client.get(verify_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(["otp"], list(response.context["form"].fields.keys()))
        self.assertContains(response, backup_url)

        # enter invalid OTP
        response = self.client.post(verify_url, {"otp": "nope"})
        self.assertFormError(response.context["form"], "otp", "Incorrect OTP. Please try again.")

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
        self.assertFormError(response.context["form"], "token", "Invalid backup token. Please try again.")

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
            response.context["form"],
            None,
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

    def test_two_factor_views(self):
        enable_url = reverse("orgs.user_two_factor_enable")
        tokens_url = reverse("orgs.user_two_factor_tokens")
        disable_url = reverse("orgs.user_two_factor_disable")

        self.login(self.admin, update_last_auth_on=True)

        # view form to enable 2FA
        response = self.client.get(enable_url)
        self.assertEqual(["otp", "confirm_password", "loc"], list(response.context["form"].fields.keys()))

        # try to submit with no OTP or password
        response = self.client.post(enable_url, {})
        self.assertFormError(response.context["form"], "otp", "This field is required.")
        self.assertFormError(response.context["form"], "confirm_password", "This field is required.")

        # try to submit with invalid OTP and password
        response = self.client.post(enable_url, {"otp": "nope", "confirm_password": "wrong"})
        self.assertFormError(response.context["form"], "otp", "OTP incorrect. Please try again.")
        self.assertFormError(response.context["form"], "confirm_password", "Password incorrect.")

        # submit with valid OTP and password
        with patch("pyotp.TOTP.verify", return_value=True):
            response = self.client.post(enable_url, {"otp": "123456", "confirm_password": "Qwerty123"})
        self.assertRedirect(response, tokens_url)

        self.admin.settings.refresh_from_db()
        self.assertTrue(self.admin.settings.two_factor_enabled)

        # view backup tokens page
        response = self.client.get(tokens_url)
        self.assertContains(response, "Regenerate Tokens")

        tokens = [t.token for t in response.context["backup_tokens"]]

        # posting to that page regenerates tokens
        response = self.client.post(tokens_url)
        self.assertToast(response, "info", "Two-factor authentication backup tokens changed.")
        self.assertNotEqual(tokens, [t.token for t in response.context["backup_tokens"]])

        # view form to disable 2FA
        response = self.client.get(disable_url)
        self.assertEqual(["confirm_password", "loc"], list(response.context["form"].fields.keys()))

        # try to submit with no password
        response = self.client.post(disable_url, {})
        self.assertFormError(response.context["form"], "confirm_password", "This field is required.")

        # try to submit with invalid password
        response = self.client.post(disable_url, {"confirm_password": "wrong"})
        self.assertFormError(response.context["form"], "confirm_password", "Password incorrect.")

        # submit with valid password
        response = self.client.post(disable_url, {"confirm_password": "Qwerty123"})
        self.assertRedirect(response, reverse("orgs.user_account"))

        self.admin.settings.refresh_from_db()
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
        self.assertFormError(response.context["form"], "password", "Password incorrect.")

        # submit with real password
        response = self.client.post(confirm_url, {"password": "Qwerty123"})
        self.assertRedirect(response, tokens_url)

        response = self.client.get(tokens_url)
        self.assertEqual(200, response.status_code)

    @override_settings(USER_LOCKOUT_TIMEOUT=1, USER_FAILED_LOGIN_LIMIT=3)
    def test_confirm_access(self):
        confirm_url = reverse("users.confirm_access") + "?next=/msg/"
        failed_url = reverse("users.user_failed")

        # try to access before logging in
        response = self.client.get(confirm_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(confirm_url)
        self.assertEqual(["password"], list(response.context["form"].fields.keys()))

        # try to submit with incorrect password
        response = self.client.post(confirm_url, {"password": "nope"})
        self.assertFormError(response.context["form"], "password", "Password incorrect.")

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
        self.assertFormError(response.context["form"], "password", "Password incorrect.")

        # and also correct ones
        response = self.client.post(confirm_url, {"password": "Qwerty123"})
        self.assertRedirect(response, "/msg/")

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
                name=f"Org {i}", timezone=ZoneInfo("Africa/Kigali"), created_by=self.user, modified_by=self.user
            )
            org.add_user(self.admin, OrgRole.ADMINISTRATOR)

        response = self.client.get(reverse("orgs.user_list"))
        self.assertEqual(200, response.status_code)

        response = self.client.post(reverse("orgs.user_delete", args=(self.editor.pk,)), {"delete": True})
        self.assertEqual(reverse("orgs.user_list"), response["Temba-Success"])

        self.editor.refresh_from_db()
        self.assertFalse(self.editor.is_active)

    def test_release(self):
        # admin doesn't "own" any orgs
        self.assertEqual(0, len(self.admin.get_owned_orgs()))

        # release all but our admin
        self.editor.release(self.customer_support)
        self.user.release(self.customer_support)
        self.agent.release(self.customer_support)

        # still a user left, our org remains active
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_active)

        # now that we are the last user, we own it now
        self.assertEqual(1, len(self.admin.get_owned_orgs()))
        self.admin.release(self.customer_support)

        # and we take our org with us
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)


class OrgTest(TembaTest):
    def test_create(self):
        new_org = Org.create(self.admin, "Cool Stuff", ZoneInfo("Africa/Kigali"))
        self.assertEqual("Cool Stuff", new_org.name)
        self.assertEqual(self.admin, new_org.created_by)
        self.assertEqual("en-us", new_org.language)
        self.assertEqual(["eng"], new_org.flow_languages)
        self.assertEqual("D", new_org.date_format)
        self.assertEqual(str(new_org.timezone), "Africa/Kigali")
        self.assertIn(self.admin, self.org.get_admins())
        self.assertEqual(f'<Org: id={new_org.id} name="Cool Stuff">', repr(new_org))

        # if timezone is US, should get MMDDYYYY dates
        new_org = Org.create(self.admin, "Cool Stuff", ZoneInfo("America/Los_Angeles"))
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
        self.org.created_by = self.user
        self.org.save(update_fields=("created_by",))

        # admins take priority
        self.assertEqual(self.admin, self.org.get_owner())

        OrgMembership.objects.filter(org=self.org, role_code="A").delete()

        # then editors etc
        self.assertEqual(self.editor, self.org.get_owner())

        OrgMembership.objects.filter(org=self.org, role_code=OrgRole.EDITOR.code).delete()
        OrgMembership.objects.filter(org=self.org, role_code=OrgRole.VIEWER.code).delete()
        OrgMembership.objects.filter(org=self.org, role_code=OrgRole.AGENT.code).delete()

        # finally defaulting to org creator
        self.assertEqual(self.user, self.org.get_owner())

    def test_get_unique_slug(self):
        self.org.slug = "allo"
        self.org.save()

        self.assertEqual(Org.get_unique_slug("foo"), "foo")
        self.assertEqual(Org.get_unique_slug("Which part?"), "which-part")
        self.assertEqual(Org.get_unique_slug("Allo"), "allo-2")

    def test_suspend_and_unsuspend(self):
        def assert_org(org, is_suspended):
            org.refresh_from_db()
            self.assertEqual(is_suspended, org.is_suspended)

        self.org.features += [Org.FEATURE_CHILD_ORGS]
        org1_child1 = self.org.create_new(self.admin, "Child 1", tzone.utc, as_child=True)
        org1_child2 = self.org.create_new(self.admin, "Child 2", tzone.utc, as_child=True)

        self.org.suspend()

        assert_org(self.org, is_suspended=True)
        assert_org(org1_child1, is_suspended=True)
        assert_org(org1_child2, is_suspended=True)
        assert_org(self.org2, is_suspended=False)

        self.assertEqual(1, self.org.incidents.filter(incident_type="org:suspended", ended_on=None).count())
        self.assertEqual(1, self.admin.notifications.filter(notification_type="incident:started").count())

        self.org.suspend()  # noop

        assert_org(self.org, is_suspended=True)

        self.assertEqual(1, self.org.incidents.filter(incident_type="org:suspended", ended_on=None).count())

        self.org.unsuspend()

        assert_org(self.org, is_suspended=False)
        assert_org(org1_child1, is_suspended=False)
        assert_org(self.org2, is_suspended=False)

        self.assertEqual(0, self.org.incidents.filter(incident_type="org:suspended", ended_on=None).count())

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

        settings_url = reverse("orgs.org_workspace")
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

        response = self.client.get(settings_url)
        self.assertContains(response, "Rwanda")

        # if location support is disabled in the settings, don't display country formax
        with override_settings(FEATURES=[]):
            response = self.client.get(settings_url)
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
        send_url = reverse("msgs.broadcast_to_node") + "?node=123&count=3"
        response = self.client.get(send_url)
        self.assertContains(response, expected_message)

        start_url = f"{reverse('flows.flow_start', args=[])}?flow={flow.id}"
        # we also can't start flows
        self.assertRaises(
            AssertionError,
            self.client.post,
            start_url,
            {"flow": flow.id, "contact_search": get_contact_search(query='uuid="{mark.uuid}"')},
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

        response = self.client.get(send_url)
        self.assertContains(response, expected_message)

        # we also can't start flows
        self.assertRaises(
            AssertionError,
            self.client.post,
            start_url,
            {"flow": flow.id, "contact_search": get_contact_search(query='uuid="{mark.uuid}"')},
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
            start_url,
            {"flow": flow.id, "contact_search": get_contact_search(query='uuid="{mark.uuid}"')},
        )

        mock_async_start.assert_called_once()

    def test_prometheus(self):
        # visit as viewer, no prometheus section
        self.login(self.user)
        settings_url = reverse("orgs.org_workspace")
        response = self.client.get(settings_url)

        self.assertNotContains(response, "Prometheus")

        # admin can see it though
        self.login(self.admin)

        response = self.client.get(settings_url)
        self.assertContains(response, "Prometheus")
        self.assertContains(response, "Enable")

        # enable it
        prometheus_url = reverse("orgs.org_prometheus")
        response = self.client.post(prometheus_url, {}, follow=True)
        self.assertContains(response, "Disable")

        # make sure our token is set
        self.org.refresh_from_db()
        self.assertIsNotNone(self.org.prometheus_token)

        # other admin sees it enabled too
        self.other_admin = self.create_user("other_admin@nyaruka.com")
        self.org.add_user(self.other_admin, OrgRole.ADMINISTRATOR)
        self.login(self.other_admin)

        response = self.client.get(settings_url)
        self.assertContains(response, "Prometheus")
        self.assertContains(response, "Disable")

        # now disable it
        response = self.client.post(prometheus_url, {}, follow=True)
        self.assertContains(response, "Enable")

        self.org.refresh_from_db()
        self.assertIsNone(self.org.prometheus_token)

    def test_resthooks(self):
        resthook_url = reverse("orgs.org_resthooks")

        # no hitting this page without auth
        response = self.client.get(resthook_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # get our resthook management page
        response = self.client.get(resthook_url)

        # shouldn't have any resthooks listed yet
        self.assertFalse(response.context["current_resthooks"])

        # try to create one with name that's too long
        response = self.client.post(resthook_url, {"new_slug": "x" * 100})
        self.assertFormError(
            response.context["form"], "new_slug", "Ensure this value has at most 50 characters (it has 100)."
        )

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

        # let's try to create a repeat, should fail due to duplicate slug
        response = self.client.post(resthook_url, {"new_slug": "Mother-Registration"})
        self.assertFormError(response.context["form"], "new_slug", "This event name has already been used.")

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

        # we should be linked to our parent
        self.assertEqual(self.org, sub_org.parent)
        self.assertEqual(self.admin, sub_org.created_by)

        # default values should be the same as parent
        self.assertEqual(self.org.timezone, sub_org.timezone)

    @patch("temba.orgs.tasks.perform_export.delay")
    def test_restart_stalled_exports(self, mock_org_export_task):
        mock_org_export_task.return_value = None

        message_export1 = MessageExport.create(org=self.org, user=self.admin, start_date=None, end_date=None)
        message_export1.status = Export.STATUS_FAILED
        message_export1.save(update_fields=("status",))

        message_export2 = MessageExport.create(org=self.org, user=self.admin, start_date=None, end_date=None)
        message_export2.status = Export.STATUS_COMPLETE
        message_export2.save(update_fields=("status",))

        MessageExport.create(org=self.org, user=self.admin, start_date=None, end_date=None)

        results_export1 = ResultsExport.create(org=self.org, user=self.admin, start_date=None, end_date=None)
        results_export1.status = Export.STATUS_FAILED
        results_export1.save(update_fields=("status",))

        results_export2 = ResultsExport.create(org=self.org, user=self.admin, start_date=None, end_date=None)
        results_export2.status = Export.STATUS_COMPLETE
        results_export2.save(update_fields=("status",))

        ResultsExport.create(org=self.org, user=self.admin, start_date=None, end_date=None)

        contact_export1 = ContactExport.create(org=self.org, user=self.admin)
        contact_export1.status = Export.STATUS_FAILED
        contact_export1.save(update_fields=("status",))
        contact_export2 = ContactExport.create(org=self.org, user=self.admin)
        contact_export2.status = Export.STATUS_COMPLETE
        contact_export2.save(update_fields=("status",))
        ContactExport.create(org=self.org, user=self.admin)

        two_hours_ago = timezone.now() - timedelta(hours=2)

        Export.objects.all().update(modified_on=two_hours_ago)

        restart_stalled_exports()

        self.assertEqual(3, mock_org_export_task.call_count)


class OrgDeleteTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.mock_s3 = MockS3Client()

    def create_content(self, org, user) -> list:
        # add child workspaces
        org.features = [Org.FEATURE_CHILD_ORGS]
        org.save(update_fields=("features",))
        org.create_new(user, "Child 1", "Africa/Kigali", as_child=True)
        org.create_new(user, "Child 2", "Africa/Kigali", as_child=True)

        content = []

        def add(obj):
            content.append(obj)
            return obj

        channels = self._create_channel_content(org, add)
        contacts, fields, groups = self._create_contact_content(org, add)
        flows = self._create_flow_content(org, user, channels, contacts, groups, add)
        labels = self._create_message_content(org, user, channels, contacts, groups, add)
        self._create_campaign_content(org, user, fields, groups, flows, contacts, add)
        self._create_ticket_content(org, user, contacts, flows, add)
        self._create_export_content(org, user, flows, groups, fields, labels, add)
        self._create_archive_content(org, add)

        # suspend and flag org to generate incident and notifications
        org.suspend()
        org.unsuspend()
        org.flag()
        org.unflag()
        for incident in org.incidents.all():
            add(incident)

        return content

    def _create_channel_content(self, org, add) -> tuple:
        channel1 = add(self.create_channel("TG", "Telegram", "+250785551212", org=org))
        channel2 = add(self.create_channel("A", "Android", "+1234567890", org=org))
        add(
            SyncEvent.create(
                channel2,
                dict(pending=[], retry=[], power_source="P", power_status="full", power_level="100", network_type="W"),
                [],
            )
        )
        add(ChannelDisconnectedIncidentType.get_or_create(channel2))
        add(ChannelLog.objects.create(channel=channel1, log_type=ChannelLog.LOG_TYPE_MSG_SEND))
        add(
            HTTPLog.objects.create(
                org=org, channel=channel2, log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, request_time=10, is_error=False
            )
        )

        return (channel1, channel2)

    def _create_flow_content(self, org, user, channels, contacts, groups, add) -> tuple:
        flow1 = add(self.create_flow("Registration", org=org))
        flow2 = add(self.create_flow("Goodbye", org=org))

        start1 = add(FlowStart.objects.create(org=org, flow=flow1))
        add(FlowStartCount.objects.create(start=start1, count=1))

        add(
            Trigger.create(
                org,
                user,
                flow=flow1,
                trigger_type=Trigger.TYPE_KEYWORD,
                keywords=["color"],
                match_type=Trigger.MATCH_FIRST_WORD,
                groups=groups,
            )
        )
        add(
            Trigger.create(
                org,
                user,
                flow=flow1,
                trigger_type=Trigger.TYPE_NEW_CONVERSATION,
                channel=channels[0],
                groups=groups,
            )
        )
        session1 = add(
            FlowSession.objects.create(
                uuid=uuid4(),
                org=org,
                contact=contacts[0],
                current_flow=flow1,
                status=FlowSession.STATUS_WAITING,
                output_url="http://sessions.com/123.json",
                wait_started_on=datetime(2022, 1, 1, 0, 0, 0, 0, tzone.utc),
                wait_expires_on=datetime(2022, 1, 2, 0, 0, 0, 0, tzone.utc),
                wait_resume_on_expire=False,
            )
        )
        add(
            FlowRun.objects.create(
                org=org,
                flow=flow1,
                contact=contacts[0],
                session=session1,
                status=FlowRun.STATUS_COMPLETED,
                exited_on=timezone.now(),
            )
        )
        contacts[0].current_flow = flow1
        contacts[0].save(update_fields=("current_flow",))

        flow_label1 = add(FlowLabel.create(org, user, "Cool Flows"))
        flow_label2 = add(FlowLabel.create(org, user, "Crazy Flows"))
        flow1.labels.add(flow_label1)
        flow2.labels.add(flow_label2)

        global1 = add(Global.get_or_create(org, user, "org_name", "Org Name", "Acme Ltd"))
        flow1.global_dependencies.add(global1)

        classifier1 = add(Classifier.create(org, user, WitType.slug, "Booker", {}, sync=False))
        add(
            HTTPLog.objects.create(
                classifier=classifier1,
                url="http://org2.bar/zap",
                request="GET /zap",
                response=" OK 200",
                is_error=False,
                log_type=HTTPLog.CLASSIFIER_CALLED,
                request_time=10,
                org=org,
            )
        )
        flow1.classifier_dependencies.add(classifier1)

        resthook = add(Resthook.get_or_create(org, "registration", user))
        resthook.subscribers.create(target_url="http://foo.bar", created_by=user, modified_by=user)

        add(WebHookEvent.objects.create(org=org, resthook=resthook, data={}))

        template = add(
            self.create_template(
                "hello",
                [
                    TemplateTranslation(
                        channel=channels[0],
                        locale="eng-US",
                        status=TemplateTranslation.STATUS_APPROVED,
                        external_id="1234",
                        external_locale="en_US",
                    )
                ],
                org=org,
            )
        )
        flow1.template_dependencies.add(template)

        return (flow1, flow2)

    def _create_contact_content(self, org, add) -> tuple[tuple]:
        contact1 = add(self.create_contact("Bob", phone="+5931234111111", org=org))
        contact2 = add(self.create_contact("Jim", phone="+5931234222222", org=org))

        field1 = add(self.create_field("age", "Age", org=org))
        field2 = add(self.create_field("joined", "Joined", value_type=ContactField.TYPE_DATETIME, org=org))

        group1 = add(self.create_group("Adults", query="age >= 18", org=org))
        group2 = add(self.create_group("Testers", contacts=[contact1, contact2], org=org))

        # create a contact import
        group3 = add(self.create_group("Imported", contacts=[], org=org))
        imp = ContactImport.objects.create(
            org=self.org, group=group3, mappings={}, num_records=0, created_by=self.admin, modified_by=self.admin
        )
        ContactImportBatch.objects.create(contact_import=imp, specs={}, record_start=0, record_end=0)

        return (contact1, contact2), (field1, field2), (group1, group2, group3)

    def _create_message_content(self, org, user, channels, contacts, groups, add) -> tuple:
        msg1 = add(self.create_incoming_msg(contact=contacts[0], text="hi", channel=channels[0]))
        add(self.create_outgoing_msg(contact=contacts[0], text="cool story", channel=channels[0]))
        add(self.create_outgoing_msg(contact=contacts[0], text="synced", channel=channels[1]))

        add(self.create_broadcast(user, {"eng": {"text": "Announcement"}}, contacts=contacts, groups=groups, org=org))

        scheduled = add(
            self.create_broadcast(
                user,
                {"eng": {"text": "Reminder"}},
                contacts=contacts,
                groups=groups,
                org=org,
                schedule=Schedule.create(org, timezone.now(), Schedule.REPEAT_DAILY),
            )
        )
        add(
            self.create_broadcast(
                user, {"eng": {"text": "Reminder"}}, contacts=contacts, groups=groups, org=org, parent=scheduled
            )
        )

        label1 = add(self.create_label("Spam", org=org))
        label2 = add(self.create_label("Important", org=org))

        label1.toggle_label([msg1], add=True)
        label2.toggle_label([msg1], add=True)

        return (label1, label2)

    def _create_campaign_content(self, org, user, fields, groups, flows, contacts, add):
        campaign = add(Campaign.create(org, user, "Reminders", groups[0]))
        event1 = add(
            CampaignEvent.create_flow_event(
                org, user, campaign, fields[1], offset=1, unit="W", flow=flows[0], delivery_hour="13"
            )
        )
        add(EventFire.objects.create(event=event1, contact=contacts[0], scheduled=timezone.now()))
        start1 = add(FlowStart.objects.create(org=org, flow=flows[0], campaign_event=event1))
        add(FlowStartCount.objects.create(start=start1, count=1))

    def _create_ticket_content(self, org, user, contacts, flows, add):
        ticket1 = add(self.create_ticket(contacts[0]))
        ticket1.events.create(org=org, contact=contacts[0], event_type="N", note="spam", created_by=user)

        add(self.create_ticket(contacts[0], opened_in=flows[0]))

    def _create_export_content(self, org, user, flows, groups, fields, labels, add):
        results = add(
            ResultsExport.create(
                org,
                user,
                start_date=date.today(),
                end_date=date.today(),
                flows=flows,
                with_fields=fields,
                with_groups=groups,
                responded_only=True,
                extra_urns=(),
            )
        )
        ExportFinishedNotificationType.create(results)

        contacts = add(ContactExport.create(org, user, group=groups[0]))
        ExportFinishedNotificationType.create(contacts)

        messages = add(MessageExport.create(org, user, start_date=date.today(), end_date=date.today(), label=labels[0]))
        ExportFinishedNotificationType.create(messages)

        tickets = add(
            TicketExport.create(
                org, user, start_date=date.today(), end_date=date.today(), with_groups=groups, with_fields=fields
            )
        )
        ExportFinishedNotificationType.create(tickets)

    def _create_archive_content(self, org, add):
        def create_archive(org, period, rollup=None):
            file = f"{org.id}/archive{Archive.objects.all().count()}.jsonl.gz"
            body, md5, size = jsonlgz_encode([{"id": 1}])
            archive = Archive.objects.create(
                org=org,
                url=f"http://temba-archives.aws.com/{file}",
                start_date=timezone.now(),
                build_time=100,
                archive_type=Archive.TYPE_MSG,
                period=period,
                rollup=rollup,
                size=size,
                hash=md5,
            )
            self.mock_s3.put_object("temba-archives", file, body)
            return archive

        daily = add(create_archive(org, Archive.PERIOD_DAILY))
        add(create_archive(org, Archive.PERIOD_MONTHLY, daily))

        # extra S3 file in archive dir
        self.mock_s3.put_object("temba-archives", f"{org.id}/extra_file.json", io.StringIO("[]"))

    def _exists(self, obj) -> bool:
        return obj._meta.model.objects.filter(id=obj.id).exists()

    def assertOrgActive(self, org, org_content=()):
        org.refresh_from_db()

        self.assertTrue(org.is_active)
        self.assertIsNone(org.released_on)
        self.assertIsNone(org.deleted_on)

        for o in org_content:
            self.assertTrue(self._exists(o), f"{repr(o)} should still exist")

    def assertOrgReleased(self, org, org_content=()):
        org.refresh_from_db()

        self.assertFalse(org.is_active)
        self.assertIsNotNone(org.released_on)
        self.assertIsNone(org.deleted_on)

        for o in org_content:
            self.assertTrue(self._exists(o), f"{repr(o)} should still exist")

    def assertOrgDeleted(self, org, org_content=()):
        org.refresh_from_db()

        self.assertFalse(org.is_active)
        self.assertEqual({}, org.config)
        self.assertIsNotNone(org.released_on)
        self.assertIsNotNone(org.deleted_on)

        for o in org_content:
            self.assertFalse(self._exists(o), f"{repr(o)} shouldn't still exist")

    def assertUserActive(self, user):
        user.refresh_from_db()

        self.assertTrue(user.is_active)
        self.assertNotEqual("", user.password)

    def assertUserReleased(self, user):
        user.refresh_from_db()

        self.assertFalse(user.is_active)
        self.assertEqual("", user.password)

    @mock_mailroom
    def test_release_and_delete(self, mr_mocks):
        org1_content = self.create_content(self.org, self.admin)
        org2_content = self.create_content(self.org2, self.admin2)

        org1_child1 = self.org.children.get(name="Child 1")
        org1_child2 = self.org.children.get(name="Child 2")

        # add editor to second org as agent
        self.org2.add_user(self.editor, OrgRole.AGENT)

        # can't delete an org that wasn't previously released
        with self.assertRaises(AssertionError):
            self.org.delete()

        self.assertOrgActive(self.org, org1_content)
        self.assertOrgActive(self.org2, org2_content)

        self.org.release(self.customer_support)

        # org and its children should be marked for deletion
        self.assertOrgReleased(self.org, org1_content)
        self.assertOrgReleased(org1_child1)
        self.assertOrgReleased(org1_child2)
        self.assertOrgActive(self.org2, org2_content)

        self.assertUserReleased(self.admin)
        self.assertUserActive(self.editor)  # because they're also in org #2
        self.assertUserReleased(self.user)
        self.assertUserReleased(self.agent)
        self.assertUserReleased(self.admin)
        self.assertUserActive(self.admin2)

        delete_released_orgs()

        self.assertOrgReleased(self.org, org1_content)  # deletion hasn't occured yet because releasing was too soon
        self.assertOrgReleased(org1_child1)
        self.assertOrgReleased(org1_child2)
        self.assertOrgActive(self.org2, org2_content)

        # make it look like released orgs were released over a week ago
        Org.objects.exclude(released_on=None).update(released_on=timezone.now() - timedelta(days=8))

        with patch("temba.utils.s3.client", return_value=self.mock_s3):
            delete_released_orgs()

        self.assertOrgDeleted(self.org, org1_content)
        self.assertOrgDeleted(org1_child1)
        self.assertOrgDeleted(org1_child2)
        self.assertOrgActive(self.org2, org2_content)

        # only org 2 files left in S3
        self.assertEqual(
            [
                ("temba-archives", f"{self.org2.id}/archive2.jsonl.gz"),
                ("temba-archives", f"{self.org2.id}/archive3.jsonl.gz"),
                ("temba-archives", f"{self.org2.id}/extra_file.json"),
            ],
            list(self.mock_s3.objects.keys()),
        )

        # we don't actually delete org objects but at this point there should be no related fields preventing that
        Model.delete(org1_child1)
        Model.delete(org1_child2)
        Model.delete(self.org)

        # releasing an already released org won't do anything
        prev_released_on = self.org.released_on
        self.org.release(self.customer_support)
        self.assertEqual(prev_released_on, self.org.released_on)


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

        anon_id = f"{contact.id:010}"

        response = self.client.get(reverse("contacts.contact_list"))

        # phone not in the list
        self.assertNotContains(response, "788 123 123")

        # but the id is
        self.assertContains(response, anon_id)

        # create an outgoing message, check number doesn't appear in outbox
        msg1 = self.create_outgoing_msg(contact, "hello", status="Q")

        response = self.client.get(reverse("msgs.msg_outbox"))

        self.assertEqual(set(response.context["object_list"]), {msg1})
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, anon_id)

        # create an incoming message, check number doesn't appear in inbox
        msg2 = self.create_incoming_msg(contact, "ok")

        response = self.client.get(reverse("msgs.msg_inbox"))

        self.assertEqual(set(response.context["object_list"]), {msg2})
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, anon_id)

        # create an incoming flow message, check number doesn't appear in inbox
        flow = self.create_flow("Test")
        msg3 = self.create_incoming_msg(contact, "ok", flow=flow)

        response = self.client.get(reverse("msgs.msg_flow"))

        self.assertEqual(set(response.context["object_list"]), {msg3})
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, anon_id)

        # check contact detail page
        response = self.client.get(reverse("contacts.contact_read", args=[contact.uuid]))
        self.assertNotContains(response, "788 123 123")
        self.assertContains(response, anon_id)


class OrgCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_manage_accounts(self):
        accounts_url = reverse("orgs.org_manage_accounts")
        settings_url = reverse("orgs.org_workspace")

        # nobody can access if we don't have users feature
        self.login(self.admin)
        self.assertRedirect(self.client.get(accounts_url), settings_url)

        self.org.features = [Org.FEATURE_USERS]
        self.org.save(update_fields=("features",))

        # create invitations
        invitation1 = Invitation.objects.create(
            org=self.org, email="norkans7@gmail.com", user_group="A", created_by=self.admin, modified_by=self.admin
        )
        invitation2 = Invitation.objects.create(
            org=self.org, email="bob@tickets.com", user_group="T", created_by=self.admin, modified_by=self.admin
        )

        # add a second editor to the org
        editor2 = self.create_user("editor2@nyaruka.com", first_name="Edwina")
        self.org.add_user(editor2, OrgRole.EDITOR)

        # only admins can access
        self.assertRequestDisallowed(accounts_url, [None, self.user, self.editor])

        # order should be users by email, then invitations by email
        expected_fields = []
        for user in self.org.users.order_by("email"):
            expected_fields.extend([f"user_{user.id}_role", f"user_{user.id}_remove"])
        for inv in self.org.invitations.order_by("email"):
            expected_fields.extend([f"invite_{inv.id}_role", f"invite_{inv.id}_remove"])

        response = self.assertUpdateFetch(accounts_url, [self.admin], form_fields=expected_fields)

        self.assertEqual("A", response.context["form"].fields[f"user_{self.admin.id}_role"].initial)
        self.assertEqual("E", response.context["form"].fields[f"user_{self.editor.id}_role"].initial)
        self.assertEqual("V", response.context["form"].fields[f"user_{self.user.id}_role"].initial)
        self.assertEqual("T", response.context["form"].fields[f"user_{self.agent.id}_role"].initial)

        # only a user which is already a viewer has the option to stay a viewer
        self.assertEqual(
            [("A", "Administrator"), ("E", "Editor"), ("T", "Agent")],
            response.context["form"].fields[f"user_{self.admin.id}_role"].choices,
        )
        self.assertEqual(
            [("A", "Administrator"), ("E", "Editor"), ("T", "Agent"), ("V", "Viewer")],
            response.context["form"].fields[f"user_{self.user.id}_role"].choices,
        )

        self.assertContains(response, "norkans7@gmail.com")

        # give users an API token
        APIToken.get_or_create(self.org, self.admin)
        APIToken.get_or_create(self.org, self.editor)
        APIToken.get_or_create(self.org, editor2)

        # leave admin, editor and agent as is, but change user to an editor too, and remove the second editor
        response = self.assertUpdateSubmit(
            accounts_url,
            self.admin,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.editor.id}_role": "E",
                f"user_{self.user.id}_role": "E",
                f"user_{editor2.id}_role": "E",
                f"user_{editor2.id}_remove": "1",
                f"user_{self.agent.id}_role": "T",
            },
        )
        self.assertRedirect(response, reverse("orgs.org_manage_accounts"))

        self.assertEqual({self.admin, self.agent, self.editor, self.user}, set(self.org.users.all()))
        self.assertEqual({self.admin}, set(self.org.get_users(roles=[OrgRole.ADMINISTRATOR])))
        self.assertEqual({self.user, self.editor}, set(self.org.get_users(roles=[OrgRole.EDITOR])))
        self.assertEqual(set(), set(self.org.get_users(roles=[OrgRole.VIEWER])))
        self.assertEqual({self.agent}, set(self.org.get_users(roles=[OrgRole.AGENT])))

        # our second editors API token should be deleted
        self.assertEqual(self.admin.api_tokens.filter(is_active=True).count(), 1)
        self.assertEqual(self.editor.api_tokens.filter(is_active=True).count(), 1)
        self.assertEqual(editor2.api_tokens.filter(is_active=True).count(), 0)

        # pretend our first invite was acted on
        invitation1.release()

        # no longer appears in list
        response = self.client.get(accounts_url)
        self.assertNotContains(response, "norkans7@gmail.com")

        # try to remove ourselves as admin
        response = self.assertUpdateSubmit(
            accounts_url,
            self.admin,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.admin.id}_remove": "1",
                f"user_{self.editor.id}_role": "E",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "T",
            },
            form_errors={"__all__": "A workspace must have at least one administrator."},
            object_unchanged=self.org,
        )

        # try to downgrade ourselves to an editor
        response = self.assertUpdateSubmit(
            accounts_url,
            self.admin,
            {
                f"user_{self.admin.id}_role": "E",
                f"user_{self.editor.id}_role": "E",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "T",
            },
            form_errors={"__all__": "A workspace must have at least one administrator."},
            object_unchanged=self.org,
        )

        # finally upgrade agent to admin, downgrade editor to agent, remove ourselves entirely and remove last invite
        response = self.assertUpdateSubmit(
            accounts_url,
            self.admin,
            {
                f"user_{self.admin.id}_role": "A",
                f"user_{self.admin.id}_remove": "1",
                f"user_{self.editor.id}_role": "T",
                f"user_{self.user.id}_role": "E",
                f"user_{self.agent.id}_role": "A",
                f"invite_{invitation2.id}_remove": "1",
            },
        )

        # we should be redirected to chooser page
        self.assertRedirect(response, reverse("orgs.org_choose"))

        self.assertEqual(0, self.org.invitations.filter(is_active=True).count())

        # and removed from this org
        self.org.refresh_from_db()
        self.assertEqual(set(self.org.users.all()), {self.agent, self.editor, self.user})
        self.assertEqual({self.agent}, set(self.org.get_users(roles=[OrgRole.ADMINISTRATOR])))
        self.assertEqual({self.user}, set(self.org.get_users(roles=[OrgRole.EDITOR])))
        self.assertEqual(set(), set(self.org.get_users(roles=[OrgRole.VIEWER])))
        self.assertEqual({self.editor}, set(self.org.get_users(roles=[OrgRole.AGENT])))

        # editor will have lost their API tokens
        self.editor.refresh_from_db()
        self.assertEqual(0, self.editor.api_tokens.filter(is_active=True).count())

        # and all our API tokens for the admin are deleted
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.api_tokens.filter(is_active=True).count(), 0)

    def test_manage_children(self):
        children_url = reverse("orgs.org_sub_orgs")

        # give our org the multi users feature
        self.org.features = [Org.FEATURE_USERS, Org.FEATURE_CHILD_ORGS]
        self.org.save()

        # add a sub org
        child = Org.objects.create(
            name="Child Workspace",
            timezone=ZoneInfo("US/Pacific"),
            flow_languages=["eng"],
            created_by=self.admin,
            modified_by=self.admin,
            parent=self.org,
        )
        child.initialize()
        child.add_user(self.admin, OrgRole.ADMINISTRATOR)

        child_accounts_url = reverse("orgs.org_manage_accounts_sub_org") + f"?org={child.id}"

        self.assertRequestDisallowed(children_url, [None, self.user, self.editor, self.agent])
        response = self.assertListFetch(children_url, [self.admin], choose_org=self.org)
        self.assertContains(response, "Child Workspace")
        self.assertContains(response, child_accounts_url)

        # only admin for parent can see account page for child
        self.assertRequestDisallowed(child_accounts_url, [None, self.user, self.editor, self.agent, self.admin2])

        self.assertUpdateFetch(
            child_accounts_url,
            [self.admin],
            form_fields=[f"user_{self.admin.id}_role", f"user_{self.admin.id}_remove"],
            choose_org=self.org,
        )

    def test_menu(self):
        menu_url = reverse("orgs.org_menu")

        self.child = Org.objects.create(
            name="Child Workspace",
            timezone=ZoneInfo("US/Pacific"),
            flow_languages=["eng"],
            created_by=self.admin,
            modified_by=self.admin,
            parent=self.org,
        )
        self.child.initialize()
        self.child.add_user(self.admin, OrgRole.ADMINISTRATOR)

        self.assertPageMenu(
            menu_url,
            self.admin,
            [
                ("Workspace", ["Nyaruka", "Sign Out", "Child Workspace"]),
                "Messages",
                "Contacts",
                "Flows",
                "Triggers",
                "Campaigns",
                "Tickets",
                ("Notifications", []),
                "Settings",
            ],
            choose_org=self.org,
        )
        self.assertPageMenu(
            f"{menu_url}settings/",
            self.admin,
            [
                "Nyaruka",
                "Account",
                "Resthooks",
                "Incidents",
                "Export",
                "Import",
                ("Channels", ["New Channel", "Test Channel"]),
                ("Classifiers", ["New Classifier"]),
                ("Archives", ["Messages", "Flow Runs"]),
            ],
            choose_org=self.org,
        )

        # agents should only see tickets and settings
        self.assertPageMenu(
            menu_url,
            self.agent,
            [
                ("Workspace", ["Nyaruka", "Sign Out"]),
                "Tickets",
                ("Notifications", []),
                "Settings",
            ],
        )

        # customer support without an org will see settings as profile, and staff section
        self.assertPageMenu(menu_url, self.customer_support, ["Settings", "Staff"])

        self.assertPageMenu(f"{menu_url}staff/", self.customer_support, ["Workspaces", "Users"])

        # if our org has new orgs but not child orgs, we should have a New Workspace button in the menu
        self.org.features = [Org.FEATURE_NEW_ORGS]
        self.org.save()

        self.assertPageMenu(
            menu_url,
            self.admin,
            [
                ("Workspace", ["Nyaruka", "Sign Out", "Child Workspace", "New Workspace"]),
                "Messages",
                "Contacts",
                "Flows",
                "Triggers",
                "Campaigns",
                "Tickets",
                ("Notifications", []),
                "Settings",
            ],
            choose_org=self.org,
        )

        # confirm no notifications
        self.login(self.admin)
        menu = self.client.get(menu_url).json()["results"]
        self.assertEqual(None, menu[8].get("bubble"))

        # flag our org to create a notification
        self.org.flag()
        menu = self.client.get(menu_url).json()["results"]
        self.assertEqual("tomato", menu[8]["bubble"])

    def test_read(self):
        read_url = reverse("orgs.org_read", args=[self.org.id])

        # make our second org a child
        self.org2.parent = self.org
        self.org2.save()

        response = self.assertStaffOnly(read_url)

        # we should have a child in our context
        self.assertEqual(1, len(response.context["children"]))

        # we should have options to flag and suspend
        self.assertContentMenu(read_url, self.customer_support, ["Edit", "Flag", "Suspend", "Verify", "-", "Service"])

        # flag and content menu option should be inverted
        self.org.flag()
        self.org.suspend()

        self.assertContentMenu(
            read_url, self.customer_support, ["Edit", "Unflag", "Unsuspend", "Verify", "-", "Service"]
        )

        # no menu for inactive orgs
        self.org.is_active = False
        self.org.save()
        self.assertContentMenu(read_url, self.customer_support, [])

    def test_workspace(self):
        workspace_url = reverse("orgs.org_workspace")

        self.assertRequestDisallowed(workspace_url, [None, self.agent])
        response = self.assertListFetch(workspace_url, [self.user, self.editor, self.admin])

        # make sure we have the appropriate number of sections
        self.assertEqual(6, len(response.context["formax"].sections))

        self.assertPageMenu(
            f"{reverse('orgs.org_menu')}settings/",
            self.admin,
            [
                "Nyaruka",
                "Account",
                "Resthooks",
                "Incidents",
                "Export",
                "Import",
                ("Channels", ["New Channel", "Test Channel"]),
                ("Classifiers", ["New Classifier"]),
                ("Archives", ["Messages", "Flow Runs"]),
            ],
        )

        # enable child workspaces and users
        self.org.features = [Org.FEATURE_USERS, Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        self.child_org = Org.objects.create(
            name="Child Org",
            timezone=ZoneInfo("Africa/Kigali"),
            country=self.org.country,
            created_by=self.user,
            modified_by=self.user,
            parent=self.org,
        )

        with self.assertNumQueries(9):
            response = self.client.get(workspace_url)

        # should have an extra menu options for workspaces and users
        self.assertPageMenu(
            f"{reverse('orgs.org_menu')}settings/",
            self.admin,
            [
                "Nyaruka",
                "Workspaces (1)",
                "Dashboard",
                "Account",
                "Users (4)",
                "Resthooks",
                "Incidents",
                "Export",
                "Import",
                ("Channels", ["New Channel", "Test Channel"]),
                ("Classifiers", ["New Classifier"]),
                ("Archives", ["Messages", "Flow Runs"]),
            ],
        )

    def test_flow_smtp(self):
        self.login(self.admin)

        settings_url = reverse("orgs.org_workspace")
        config_url = reverse("orgs.org_flow_smtp")

        # orgs without SMTP settings see default from address
        response = self.client.get(settings_url)
        self.assertContains(response, "Emails sent from flows will be sent from <b>no-reply@temba.io</b>.")
        self.assertEqual("no-reply@temba.io", response.context["from_email_default"])  # from settings
        self.assertEqual(None, response.context["from_email_custom"])

        # make org a child to a parent that alsos doesn't have SMTP settings
        self.org.parent = self.org2
        self.org.save(update_fields=("parent",))

        response = self.client.get(config_url)
        self.assertContains(response, "You can add your own SMTP settings for emails sent from flows.")
        self.assertEqual("no-reply@temba.io", response.context["from_email_default"])
        self.assertIsNone(response.context["from_email_custom"])

        # give parent custom SMTP settings
        self.org2.flow_smtp = "smtp://bob%40acme.com:secret@example.com/?from=bob%40acme.com&tls=true"
        self.org2.save(update_fields=("flow_smtp",))

        response = self.client.get(settings_url)
        self.assertContains(response, "Emails sent from flows will be sent from <b>bob@acme.com</b>.")

        response = self.client.get(config_url)
        self.assertContains(response, "You can add your own SMTP settings for emails sent from flows.")
        self.assertEqual("bob@acme.com", response.context["from_email_default"])
        self.assertIsNone(response.context["from_email_custom"])

        # try submitting without any data
        response = self.client.post(config_url, {})
        self.assertFormError(response.context["form"], "from_email", "This field is required.")
        self.assertFormError(response.context["form"], "host", "This field is required.")
        self.assertFormError(response.context["form"], "username", "This field is required.")
        self.assertFormError(response.context["form"], "password", "This field is required.")
        self.assertFormError(response.context["form"], "port", "This field is required.")
        self.assertEqual(len(mail.outbox), 0)

        # try submitting an invalid from address
        response = self.client.post(config_url, {"from_email": "foobar.com"})
        self.assertFormError(response.context["form"], "from_email", "Not a valid email address.")
        self.assertEqual(len(mail.outbox), 0)

        # mock email sending so test send fails
        with patch("temba.utils.email.send.send_email") as mock_send:
            mock_send.side_effect = smtplib.SMTPException("boom")

            response = self.client.post(
                config_url,
                {
                    "from_email": "foo@bar.com",
                    "host": "smtp.example.com",
                    "username": "support@example.com",
                    "password": "secret",
                    "port": "465",
                },
            )
            self.assertFormError(response.context["form"], None, "SMTP settings test failed with error: boom")
            self.assertEqual(len(mail.outbox), 0)

            mock_send.side_effect = Exception("Unexpected Error")
            response = self.client.post(
                config_url,
                {
                    "from_email": "foo@bar.com",
                    "host": "smtp.example.com",
                    "username": "support@example.com",
                    "password": "secret",
                    "port": "465",
                },
                follow=True,
            )
            self.assertFormError(response.context["form"], None, "SMTP settings test failed.")
            self.assertEqual(len(mail.outbox), 0)

        # submit with valid fields
        self.client.post(
            config_url,
            {
                "from_email": "  foo@bar.com  ",  # check trimming
                "host": "smtp.example.com",
                "username": "support@example.com",
                "password": " secret ",
                "port": "465",
            },
        )
        self.assertEqual(len(mail.outbox), 1)

        self.org.refresh_from_db()
        self.assertEqual(
            r"smtp://support%40example.com:secret@smtp.example.com:465/?from=foo%40bar.com&tls=true", self.org.flow_smtp
        )

        response = self.client.get(settings_url)
        self.assertContains(response, "Emails sent from flows will be sent from <b>foo@bar.com</b>.")

        response = self.client.get(config_url)
        self.assertContains(response, "If you no longer want to use these SMTP settings")
        self.assertEqual("bob@acme.com", response.context["from_email_default"])
        self.assertEqual("foo@bar.com", response.context["from_email_custom"])

        # submit with disconnect flag
        self.client.post(config_url, {"disconnect": "true"})

        self.org.refresh_from_db()
        self.assertIsNone(self.org.flow_smtp)

        response = self.client.get(settings_url)
        self.assertContains(response, "Emails sent from flows will be sent from <b>bob@acme.com</b>.")

    def test_join(self):
        # if invitation secret is invalid, redirect to root
        response = self.client.get(reverse("orgs.org_join", args=["invalid"]))
        self.assertRedirect(response, reverse("public.public_index"))

        invitation = Invitation.objects.create(
            org=self.org,
            user_group="E",
            email="edwin@nyaruka.com",
            created_by=self.admin,
            modified_by=self.admin,
        )

        join_url = reverse("orgs.org_join", args=[invitation.secret])
        join_signup_url = reverse("orgs.org_join_signup", args=[invitation.secret])
        join_accept_url = reverse("orgs.org_join_accept", args=[invitation.secret])

        # if no user exists then we redirect to the join signup page
        response = self.client.get(join_url)
        self.assertRedirect(response, join_signup_url)

        user = self.create_user("edwin@nyaruka.com")
        self.login(user)

        response = self.client.get(join_url)
        self.assertRedirect(response, join_accept_url)

        # but only if they're the currently logged in user
        self.login(self.admin)

        response = self.client.get(join_url)
        self.assertContains(response, "Sign in to join the <b>Nyaruka</b> workspace")
        self.assertContains(response, f"/users/login/?next={join_accept_url}")

        # should be logged out as the other user
        self.assertEqual(0, len(self.client.session.keys()))

    def test_join_signup(self):
        # if invitation secret is invalid, redirect to root
        response = self.client.get(reverse("orgs.org_join_signup", args=["invalid"]))
        self.assertRedirect(response, reverse("public.public_index"))

        invitation = Invitation.objects.create(
            org=self.org,
            user_group="A",
            email="administrator@trileet.com",
            created_by=self.admin,
            modified_by=self.admin,
        )

        join_signup_url = reverse("orgs.org_join_signup", args=[invitation.secret])
        join_url = reverse("orgs.org_join", args=[invitation.secret])

        # if user already exists then we redirect back to join
        response = self.client.get(join_signup_url)
        self.assertRedirect(response, join_url)

        invitation = Invitation.objects.create(
            org=self.org,
            user_group="E",
            email="edwin@nyaruka.com",
            created_by=self.admin,
            modified_by=self.admin,
        )

        join_signup_url = reverse("orgs.org_join_signup", args=[invitation.secret])
        join_url = reverse("orgs.org_join", args=[invitation.secret])

        response = self.client.get(join_signup_url)
        self.assertContains(response, "edwin@nyaruka.com")
        self.assertEqual(["first_name", "last_name", "password", "loc"], list(response.context["form"].fields.keys()))

        response = self.client.post(join_signup_url, {})
        self.assertFormError(response.context["form"], "first_name", "This field is required.")
        self.assertFormError(response.context["form"], "last_name", "This field is required.")
        self.assertFormError(response.context["form"], "password", "This field is required.")

        response = self.client.post(join_signup_url, {"first_name": "Ed", "last_name": "Edits", "password": "Flows123"})
        self.assertRedirect(response, "/org/start/")

        invitation.refresh_from_db()
        self.assertFalse(invitation.is_active)

    def test_join_accept(self):
        # only authenticated users can access page
        response = self.client.get(reverse("orgs.org_join_accept", args=["invalid"]))
        self.assertLoginRedirect(response)

        # if invitation secret is invalid, redirect to root
        self.login(self.admin)
        response = self.client.get(reverse("orgs.org_join_accept", args=["invalid"]))
        self.assertRedirect(response, reverse("public.public_index"))

        invitation = Invitation.objects.create(
            org=self.org,
            user_group="E",
            email="edwin@nyaruka.com",
            created_by=self.admin,
            modified_by=self.admin,
        )

        join_accept_url = reverse("orgs.org_join_accept", args=[invitation.secret])
        join_url = reverse("orgs.org_join", args=[invitation.secret])

        # if user doesn't exist then redirect back to join
        response = self.client.get(join_accept_url)
        self.assertRedirect(response, join_url)

        user = self.create_user("edwin@nyaruka.com")

        # if user exists but we're logged in as other user, also redirect
        response = self.client.get(join_accept_url)
        self.assertRedirect(response, join_url)

        self.login(user)

        response = self.client.get(join_accept_url)
        self.assertContains(response, "You have been invited to join the <b>Nyaruka</b> workspace.")

        response = self.client.post(join_accept_url)
        self.assertRedirect(response, "/org/start/")

        invitation.refresh_from_db()
        self.assertFalse(invitation.is_active)

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
        self.assertToast(response, "info", "Workspace successfully created.")

        org = Org.objects.get(name="Oculus")
        self.assertEqual(org.date_format, Org.DATE_FORMAT_DAY_FIRST)

        # check user exists and is admin
        self.assertEqual(OrgRole.ADMINISTRATOR, org.get_user_role(User.objects.get(username="john@carmack.com")))
        self.assertEqual(OrgRole.ADMINISTRATOR, org.get_user_role(User.objects.get(username="tito@nyaruka.com")))

        # try a new org with a user that already exists instead
        del post_data["password"]
        post_data["name"] = "id Software"

        response = self.client.post(grant_url, post_data, follow=True)
        self.assertToast(response, "info", "Workspace successfully created.")

        org = Org.objects.get(name="id Software")
        self.assertEqual(org.date_format, Org.DATE_FORMAT_DAY_FIRST)

        self.assertEqual(OrgRole.ADMINISTRATOR, org.get_user_role(User.objects.get(username="john@carmack.com")))
        self.assertEqual(OrgRole.ADMINISTRATOR, org.get_user_role(User.objects.get(username="tito@nyaruka.com")))

        # try a new org with US timezone
        post_data["name"] = "Bulls"
        post_data["timezone"] = "America/Chicago"
        response = self.client.post(grant_url, post_data, follow=True)

        self.assertToast(response, "info", "Workspace successfully created.")

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
        self.assertFormError(response.context["form"], "email", "This field is required.")

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
        self.assertFormError(response.context["form"], "email", "Enter a valid email address.")

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
            response.context["form"], "first_name", "Ensure this value has at most 150 characters (it has 159)."
        )
        self.assertFormError(
            response.context["form"], "last_name", "Ensure this value has at most 150 characters (it has 162)."
        )
        self.assertFormError(
            response.context["form"], "name", "Ensure this value has at most 128 characters (it has 136)."
        )
        self.assertFormError(
            response.context["form"],
            "email",
            ["Enter a valid email address.", "Ensure this value has at most 150 characters (it has 159)."],
        )

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
        self.assertFormError(response.context["form"], None, "Login already exists, please do not include password.")

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
        self.assertFormError(response.context["form"], None, "Password required for new login.")

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
            response.context["form"], None, "This password is too short. It must contain at least 8 characters."
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
        self.assertEqual(org.timezone, ZoneInfo("Africa/Kigali"))

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
        edit_url = reverse("orgs.user_edit")

        response = self.client.get(signup_url + "?%s" % urlencode({"email": "address@example.com"}))
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].fields)
        self.assertEqual(response.context["view"].derive_initial()["email"], "address@example.com")

        response = self.client.get(signup_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.context["form"].fields)

        # submit with missing fields
        response = self.client.post(signup_url, {})
        self.assertFormError(response.context["form"], "name", "This field is required.")
        self.assertFormError(response.context["form"], "first_name", "This field is required.")
        self.assertFormError(response.context["form"], "last_name", "This field is required.")
        self.assertFormError(response.context["form"], "email", "This field is required.")
        self.assertFormError(response.context["form"], "password", "This field is required.")
        self.assertFormError(response.context["form"], "timezone", "This field is required.")

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
        self.assertFormError(response.context["form"], "email", "Enter a valid email address.")
        self.assertFormError(
            response.context["form"], "password", "This password is too short. It must contain at least 8 characters."
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
        self.assertFormError(response.context["form"], "password", "This password is too common.")

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
        self.assertFormError(response.context["form"], "password", "This password is entirely numeric.")

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
        self.assertEqual(org.timezone, ZoneInfo("Africa/Kigali"))
        self.assertEqual(str(org), "Relieves World")

        # of which our user is an administrator, and can generate an API token
        self.assertIn(user, org.get_admins())
        self.assertIsNotNone(user.get_api_token(org))

        # check default org content was created correctly
        system_fields = set(org.fields.filter(is_system=True).values_list("key", flat=True))
        system_groups = set(org.groups.filter(is_system=True).values_list("name", flat=True))
        sample_flows = set(org.flows.values_list("name", flat=True))

        self.assertEqual({"created_on", "last_seen_on"}, system_fields)
        self.assertEqual({"Active", "Archived", "Blocked", "Stopped", "Open Tickets"}, system_groups)
        self.assertEqual(
            {"Sample Flow - Order Status Checker", "Sample Flow - Satisfaction Survey", "Sample Flow - Simple Poll"},
            sample_flows,
        )

        # should now be able to go to channels page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertEqual(200, response.status_code)

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
        self.assertFormError(response.context["form"], "email", "That email address is already used")

        # if we hit /login we'll be taken back to the channel page
        response = self.client.get(reverse("users.user_check_login"))
        self.assertRedirect(response, reverse("orgs.org_choose"))

        # but if we log out, same thing takes us to the login page
        self.client.logout()

        response = self.client.get(reverse("users.user_check_login"))
        self.assertRedirect(response, reverse("users.user_login"))

        # try going to the org home page, no dice
        response = self.client.get(reverse("orgs.org_workspace"))
        self.assertRedirect(response, reverse("users.user_login"))

        # log in as the user
        self.client.login(username="myal12345678901234567890@relieves.org", password="HelloWorld1")
        response = self.client.get(reverse("orgs.org_workspace"))

        self.assertEqual(200, response.status_code)

        # try changing our username, wrong password
        response = self.client.post(edit_url, {"email": "myal@wr.org", "current_password": "HelloWorld"})
        self.assertEqual(200, response.status_code)
        self.assertFormError(
            response.context["form"],
            "current_password",
            "Please enter your password to save changes.",
        )

        # bad new password
        response = self.client.post(
            edit_url, {"email": "myal@wr.org", "current_password": "HelloWorld1", "new_password": "passwor"}
        )
        self.assertEqual(200, response.status_code)
        self.assertFormError(
            response.context["form"],
            "new_password",
            "This password is too short. It must contain at least 8 characters.",
        )

        User.objects.create(username="bill@msn.com", email="bill@msn.com")

        # dupe user
        response = self.client.post(edit_url, {"email": "bill@MSN.com", "current_password": "HelloWorld1"})
        self.assertEqual(200, response.status_code)
        self.assertFormError(response.context["form"], "email", "Sorry, that email address is already taken.")

        post_data = dict(
            email="myal@wr.org",
            first_name="Myal",
            last_name="Greene",
            language="en-us",
            current_password="HelloWorld1",
        )
        response = self.client.post(edit_url, post_data, HTTP_X_FORMAX=True)
        self.assertEqual(200, response.status_code)

        self.assertTrue(User.objects.get(username="myal@wr.org"))
        self.assertTrue(User.objects.get(email="myal@wr.org"))
        self.assertFalse(User.objects.filter(username="myal@relieves.org"))
        self.assertFalse(User.objects.filter(email="myal@relieves.org"))

    def test_create_new(self):
        children_url = reverse("orgs.org_sub_orgs")
        create_url = reverse("orgs.org_create")

        self.login(self.admin)

        # by default orgs don't have this feature
        response = self.client.get(children_url)
        self.assertContentMenu(children_url, self.admin, [])

        # trying to access the modal directly should redirect
        response = self.client.get(create_url)
        self.assertRedirect(response, "/org/workspace/")

        self.org.features = [Org.FEATURE_NEW_ORGS]
        self.org.save(update_fields=("features",))

        response = self.client.get(children_url)
        self.assertContentMenu(children_url, self.admin, ["New Workspace"])

        # give org2 the same feature
        self.org2.features = [Org.FEATURE_NEW_ORGS]
        self.org2.save(update_fields=("features",))

        # since we can only create new orgs, we don't show type as an option
        self.assertRequestDisallowed(create_url, [None, self.user, self.editor, self.agent])
        self.assertCreateFetch(create_url, [self.admin], form_fields=["name", "timezone"])

        # try to submit an empty form
        response = self.assertCreateSubmit(
            create_url,
            self.admin,
            {},
            form_errors={"name": "This field is required.", "timezone": "This field is required."},
        )

        # submit with valid values to create a new org...
        response = self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "My Other Org", "timezone": "Africa/Nairobi"},
            new_obj_query=Org.objects.filter(name="My Other Org", parent=None),
        )

        new_org = Org.objects.get(name="My Other Org")
        self.assertEqual([], new_org.features)
        self.assertEqual("Africa/Nairobi", str(new_org.timezone))
        self.assertEqual(OrgRole.ADMINISTRATOR, new_org.get_user_role(self.admin))

        # should be now logged into that org
        self.assertRedirect(response, "/org/start/")
        response = self.client.get("/org/start/")
        self.assertEqual(str(new_org.id), response.headers["X-Temba-Org"])

    def test_create_child(self):
        children_url = reverse("orgs.org_sub_orgs")
        create_url = reverse("orgs.org_create")

        self.login(self.admin)

        # by default orgs don't have the new_orgs or child_orgs feature
        response = self.client.get(children_url)
        self.assertContentMenu(children_url, self.admin, [])

        # trying to access the modal directly should redirect
        response = self.client.get(create_url)
        self.assertRedirect(response, "/org/workspace/")

        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        response = self.client.get(children_url)
        self.assertContentMenu(children_url, self.admin, ["New Workspace"])

        # give org2 the same feature
        self.org2.features = [Org.FEATURE_CHILD_ORGS]
        self.org2.save(update_fields=("features",))

        # since we can only create child orgs, we don't show type as an option
        self.assertRequestDisallowed(create_url, [None, self.user, self.editor, self.agent])
        self.assertCreateFetch(create_url, [self.admin], form_fields=["name", "timezone"])

        # try to submit an empty form
        response = self.assertCreateSubmit(
            create_url,
            self.admin,
            {},
            form_errors={"name": "This field is required.", "timezone": "This field is required."},
        )

        # submit with valid values to create a child org...
        response = self.assertCreateSubmit(
            create_url,
            self.admin,
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
        self.assertRequestDisallowed(create_url, [None, self.user, self.editor, self.agent])
        self.assertCreateFetch(create_url, [self.admin], form_fields=["type", "name", "timezone"])

        # create new org
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"type": "new", "name": "New Org", "timezone": "Africa/Nairobi"},
            new_obj_query=Org.objects.filter(name="New Org", parent=None),
        )

        # create child org
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"type": "child", "name": "Child Org", "timezone": "Africa/Nairobi"},
            new_obj_query=Org.objects.filter(name="Child Org", parent=self.org),
        )

    def test_create_child_spa(self):
        create_url = reverse("orgs.org_create")

        self.login(self.admin)

        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        response = self.client.post(create_url, {"name": "Child Org", "timezone": "Africa/Nairobi"}, HTTP_TEMBA_SPA=1)

        self.assertRedirect(response, reverse("orgs.org_sub_orgs"))

    def test_child_management(self):
        sub_orgs_url = reverse("orgs.org_sub_orgs")
        menu_url = reverse("orgs.org_menu") + "settings/"

        self.login(self.admin)

        response = self.client.get(menu_url)
        self.assertNotContains(response, "Workspaces")
        self.assertNotContains(response, sub_orgs_url)

        # enable child orgs and create some child orgs
        self.org.features = [Org.FEATURE_CHILD_ORGS, Org.FEATURE_USERS]
        self.org.save(update_fields=("features",))
        child1 = self.org.create_new(self.admin, "Child Org 1", self.org.timezone, as_child=True)
        child2 = self.org.create_new(self.admin, "Child Org 2", self.org.timezone, as_child=True)

        # now we see the Workspaces menu item
        self.login(self.admin, choose_org=self.org)

        response = self.client.get(menu_url)
        self.assertContains(response, "Workspaces")
        self.assertContains(response, sub_orgs_url)

        response = self.assertListFetch(
            sub_orgs_url, [self.admin], context_objects=[child1, child2], choose_org=self.org
        )

        child1_edit_url = reverse("orgs.org_edit_sub_org") + f"?org={child1.id}"
        child1_accounts_url = reverse("orgs.org_manage_accounts_sub_org") + f"?org={child1.id}"

        self.assertContains(response, "Child Org 1")
        self.assertContains(response, child1_accounts_url)

        # we can also access the manage accounts page
        response = self.client.get(child1_accounts_url)
        self.assertEqual(200, response.status_code)

        response = self.client.get(child1_accounts_url, HTTP_TEMBA_SPA=1)
        self.assertContains(response, child1.name)

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

        self.assertEqual(reverse("orgs.org_sub_orgs"), response.url)

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

    def test_start(self):
        # the start view routes users based on their role
        start_url = reverse("orgs.org_start")

        # not authenticated, you should get a login redirect
        self.assertLoginRedirect(self.client.get(start_url))

        # now for all our roles
        self.assertRedirect(self.requestView(start_url, self.admin), "/msg/")
        self.assertRedirect(self.requestView(start_url, self.editor), "/msg/")
        self.assertRedirect(self.requestView(start_url, self.user), "/msg/")
        self.assertRedirect(self.requestView(start_url, self.agent), "/ticket/")

        # now try as customer support
        self.assertRedirect(self.requestView(start_url, self.customer_support), "/org/manage/")

        # if org isn't set, we redirect instead to choose view
        self.client.logout()
        self.org2.add_user(self.admin, OrgRole.ADMINISTRATOR)
        self.login(self.admin)
        self.assertRedirect(self.client.get(start_url), "/org/choose/")

    def test_choose(self):
        choose_url = reverse("orgs.org_choose")

        # create an inactive org which should never appear as an option
        org3 = Org.objects.create(
            name="Deactivated", timezone=tzone.utc, created_by=self.user, modified_by=self.user, is_active=False
        )
        org3.add_user(self.editor, OrgRole.EDITOR)

        # and another org that none of our users belong to
        org4 = Org.objects.create(name="Other", timezone=tzone.utc, created_by=self.user, modified_by=self.user)

        self.assertLoginRedirect(self.client.get(choose_url))

        # users with a single org are always redirected to the start page automatically
        self.assertRedirect(self.requestView(choose_url, self.admin), "/org/start/")
        self.assertRedirect(self.requestView(choose_url, self.editor), "/org/start/")
        self.assertRedirect(self.requestView(choose_url, self.user), "/org/start/")
        self.assertRedirect(self.requestView(choose_url, self.agent), "/org/start/")

        # users with no org are redirected back to the login page
        response = self.requestView(choose_url, self.non_org_user)
        self.assertLoginRedirect(response)
        response = self.client.get("/users/login/")
        self.assertContains(response, "No workspaces for this account, please contact your administrator.")

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
            response.context["form"],
            "organization",
            "Select a valid choice. That choice is not one of the available choices.",
        )

        # user clicks org 2...
        response = self.client.post(choose_url, {"organization": self.org2.id})
        self.assertRedirect(response, "/org/start/")

    def test_edit(self):
        edit_url = reverse("orgs.org_edit")

        self.assertLoginRedirect(self.client.get(edit_url))

        self.login(self.admin)

        response = self.client.get(edit_url)
        self.assertEqual(
            ["name", "timezone", "date_format", "language", "loc"], list(response.context["form"].fields.keys())
        )

        # language is only shown if there are multiple options
        with override_settings(LANGUAGES=(("en-us", "English"),)):
            response = self.client.get(edit_url)
            self.assertEqual(["name", "timezone", "date_format", "loc"], list(response.context["form"].fields.keys()))

        # try submitting with errors
        response = self.client.post(
            reverse("orgs.org_edit"),
            {"name": "", "timezone": "Bad/Timezone", "date_format": "X", "language": "klingon"},
        )
        self.assertFormError(response.context["form"], "name", "This field is required.")
        self.assertFormError(
            response.context["form"],
            "timezone",
            "Select a valid choice. Bad/Timezone is not one of the available choices.",
        )
        self.assertFormError(
            response.context["form"], "date_format", "Select a valid choice. X is not one of the available choices."
        )
        self.assertFormError(
            response.context["form"], "language", "Select a valid choice. klingon is not one of the available choices."
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

    def test_delete_child(self):
        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        child = self.org.create_new(self.admin, "Child Workspace", self.org.timezone, as_child=True)
        delete_url = reverse("orgs.org_delete_child", args=[child.id])

        self.assertRequestDisallowed(delete_url, [None, self.user, self.editor, self.agent, self.admin2])
        self.assertDeleteFetch(delete_url, [self.admin], choose_org=self.org)

        # schedule for deletion
        response = self.client.get(delete_url)
        self.assertContains(response, "You are about to delete the workspace <b>Child Workspace</b>")

        # go through with it, redirects to main workspace page
        response = self.client.post(delete_url)
        self.assertEqual(reverse("orgs.org_sub_orgs"), response["Temba-Success"])

        child.refresh_from_db()
        self.assertFalse(child.is_active)

    def test_administration(self):
        self.setUpLocations()

        manage_url = reverse("orgs.org_manage")
        update_url = reverse("orgs.org_update", args=[self.org.id])

        self.assertStaffOnly(manage_url)
        self.assertStaffOnly(update_url)

        def assertOrgFilter(query: str, expected_orgs: list):
            response = self.client.get(manage_url + query)
            self.assertIsNotNone(response.headers.get(TEMBA_MENU_SELECTION, None))
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

        # flag org
        self.client.post(update_url, {"action": "flag"})
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_flagged)

        # unflag org
        self.client.post(update_url, {"action": "unflag"})
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_flagged)

        # suspend org
        self.client.post(update_url, {"action": "suspend"})
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_suspended)

        # unsuspend org
        self.client.post(update_url, {"action": "unsuspend"})
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_suspended)

        # verify
        self.client.post(update_url, {"action": "verify"})
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_verified)

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
        self.create_channel("TWT", "Twitter", "nyaruka")
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
        inbox_url = reverse("msgs.msg_inbox")

        # without logging in, try to service our main org
        response = self.client.get(service_url, {"other_org": self.org.id, "next": inbox_url})
        self.assertLoginRedirect(response)

        response = self.client.post(service_url, {"other_org": self.org.id})
        self.assertLoginRedirect(response)

        # try logging in with a normal user
        self.login(self.admin)

        # same thing, no permission
        response = self.client.get(service_url, {"other_org": self.org.id, "next": inbox_url})
        self.assertLoginRedirect(response)

        response = self.client.post(service_url, {"other_org": self.org.id})
        self.assertLoginRedirect(response)

        # ok, log in as our cs rep
        self.login(self.customer_support)

        # getting invalid org, has no service form
        response = self.client.get(service_url, {"other_org": 325253256, "next": inbox_url})
        self.assertContains(response, "Invalid org")

        # posting invalid org just redirects back to manage page
        response = self.client.post(service_url, {"other_org": 325253256})
        self.assertRedirect(response, "/org/manage/")

        # then service our org
        response = self.client.get(service_url, {"other_org": self.org.id})
        self.assertContains(response, "You are about to service the workspace, <b>Nyaruka</b>.")

        # requesting a next page has a slightly different message
        response = self.client.get(service_url, {"other_org": self.org.id, "next": inbox_url})
        self.assertContains(response, "The page you are requesting belongs to a different workspace, <b>Nyaruka</b>.")

        response = self.client.post(service_url, {"other_org": self.org.id})
        self.assertRedirect(response, "/msg/")
        self.assertEqual(self.org.id, self.client.session["org_id"])
        self.assertTrue(self.client.session["servicing"])

        # specify redirect_url
        response = self.client.post(service_url, {"other_org": self.org.id, "next": "/flow/"})
        self.assertRedirect(response, "/flow/")

        # create a new contact
        response = self.client.post(
            reverse("contacts.contact_create"), data=dict(name="Ben Haggerty", phone="0788123123")
        )
        self.assertNoFormErrors(response)

        # make sure that contact's created on is our cs rep
        contact = Contact.objects.get(urns__path="+250788123123", org=self.org)
        self.assertEqual(self.customer_support, contact.created_by)

        self.assertEqual(self.org.id, self.client.session["org_id"])
        self.assertTrue(self.client.session["servicing"])

        # stop servicing
        response = self.client.post(service_url, {})
        self.assertRedirect(response, "/org/manage/")
        self.assertIsNone(self.client.session["org_id"])
        self.assertFalse(self.client.session["servicing"])

    def test_languages(self):
        settings_url = reverse("orgs.org_workspace")
        langs_url = reverse("orgs.org_languages")

        self.org.set_flow_languages(self.admin, ["eng"])

        response = self.requestView(settings_url, self.admin)
        self.assertEqual("English", response.context["primary_lang"])
        self.assertEqual([], response.context["other_langs"])

        self.assertRequestDisallowed(langs_url, [None, self.user, self.editor, self.agent])
        self.assertUpdateFetch(langs_url, [self.admin], form_fields=["primary_lang", "other_langs", "input_collation"])

        # initial should do a match on code only
        response = self.client.get(f"{langs_url}?initial=fra", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual([{"name": "French", "value": "fra"}], response.json()["results"])

        # try to submit as is (empty)
        self.assertUpdateSubmit(
            langs_url,
            self.admin,
            {},
            object_unchanged=self.org,
            form_errors={"primary_lang": "This field is required.", "input_collation": "This field is required."},
        )

        # give the org a primary language
        self.assertUpdateSubmit(
            langs_url,
            self.admin,
            {"primary_lang": '{"name":"French", "value":"fra"}', "input_collation": "confusables"},
        )

        self.org.refresh_from_db()
        self.assertEqual(["fra"], self.org.flow_languages)
        self.assertEqual("confusables", self.org.input_collation)

        # summary now includes this
        response = self.requestView(settings_url, self.admin)
        self.assertContains(response, "The default flow language is <b>French</b>.")
        self.assertNotContains(response, "Translations are provided in")

        # and now give it additional languages
        self.assertUpdateSubmit(
            langs_url,
            self.admin,
            {
                "primary_lang": '{"name":"French", "value":"fra"}',
                "other_langs": ['{"name":"Haitian", "value":"hat"}', '{"name":"Hausa", "value":"hau"}'],
                "input_collation": "confusables",
            },
        )

        self.org.refresh_from_db()
        self.assertEqual(["fra", "hat", "hau"], self.org.flow_languages)

        response = self.requestView(settings_url, self.admin)
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
        self.assertEqual(8, len(response.context["object_list"]))
        self.assertEqual("/staff/users/all", response.headers[TEMBA_MENU_SELECTION])

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

        response = self.requestView(delete_url, self.customer_support)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Nyaruka")

        response = self.requestView(delete_url, self.customer_support, post_data={})
        self.assertEqual(reverse("orgs.user_list"), response["Temba-Success"])

        self.editor.refresh_from_db()
        self.assertFalse(self.editor.is_active)

        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)

    def test_account(self):
        self.login(self.agent)

        response = self.client.get(reverse("orgs.user_account"))
        self.assertEqual(1, len(response.context["formax"].sections))

        self.login(self.admin)

        response = self.client.get(reverse("orgs.user_account"))
        self.assertEqual(2, len(response.context["formax"].sections))

    def test_edit(self):
        edit_url = reverse("orgs.user_edit")

        # generate a recovery token so we can check it's deleted when email changes
        RecoveryToken.objects.create(user=self.admin, token="1234567")

        # no access if anonymous
        self.assertRequestDisallowed(edit_url, [None])

        self.assertUpdateFetch(
            edit_url,
            [self.admin],
            form_fields=["first_name", "last_name", "email", "avatar", "current_password", "new_password", "language"],
        )

        # language is only shown if there are multiple options
        with override_settings(LANGUAGES=(("en-us", "English"),)):
            self.assertUpdateFetch(
                edit_url,
                [self.admin],
                form_fields=["first_name", "last_name", "email", "avatar", "current_password", "new_password"],
            )

        self.admin.settings.email_status = "V"  # mark user email as verified
        self.admin.settings.email_verification_secret = "old-email-secret"
        self.admin.settings.save()

        # try to submit without required fields
        self.assertUpdateSubmit(
            edit_url,
            self.admin,
            {},
            form_errors={
                "email": "This field is required.",
                "first_name": "This field is required.",
                "last_name": "This field is required.",
                "language": "This field is required.",
                "current_password": "Please enter your password to save changes.",
            },
            object_unchanged=self.admin,
        )

        # change the name and language
        self.assertUpdateSubmit(
            edit_url,
            self.admin,
            {
                "avatar": self.getMockImageUpload(),
                "language": "pt-br",
                "first_name": "Admin",
                "last_name": "User",
                "email": "admin@nyaruka.com",
                "current_password": "",
            },
        )

        self.admin.refresh_from_db()
        self.assertEqual("Admin User", self.admin.name)
        self.assertEqual("V", self.admin.settings.email_status)  # unchanged
        self.assertEqual("old-email-secret", self.admin.settings.email_verification_secret)  # unchanged
        self.assertEqual(1, RecoveryToken.objects.filter(user=self.admin).count())  # unchanged
        self.assertIsNotNone(self.admin.settings.avatar)
        self.assertEqual("pt-br", self.admin.settings.language)

        self.assertEqual(0, self.admin.notifications.count())

        self.admin.settings.language = "en-us"
        self.admin.settings.save()

        # try to change email without entering password
        self.assertUpdateSubmit(
            edit_url,
            self.admin,
            {
                "language": "en-us",
                "first_name": "Admin",
                "last_name": "User",
                "email": "admin@trileet.com",
                "current_password": "",
            },
            form_errors={"current_password": "Please enter your password to save changes."},
            object_unchanged=self.admin,
        )

        # submit with current password
        self.assertUpdateSubmit(
            edit_url,
            self.admin,
            {
                "language": "en-us",
                "first_name": "Admin",
                "last_name": "User",
                "email": "admin@trileet.com",
                "current_password": "Qwerty123",
            },
        )

        self.admin.refresh_from_db()
        self.admin.settings.refresh_from_db()
        self.assertEqual("admin@trileet.com", self.admin.username)
        self.assertEqual("admin@trileet.com", self.admin.email)
        self.assertEqual("U", self.admin.settings.email_status)  # because email changed
        self.assertNotEqual("old-email-secret", self.admin.settings.email_verification_secret)
        self.assertEqual(0, RecoveryToken.objects.filter(user=self.admin).count())

        # should have a email changed notification using old address
        self.assertEqual({"user:email"}, set(self.admin.notifications.values_list("notification_type", flat=True)))
        self.assertEqual("admin@nyaruka.com", self.admin.notifications.get().email_address)

        # try to change password without entering current password
        self.assertUpdateSubmit(
            edit_url,
            self.admin,
            {
                "language": "en-us",
                "first_name": "Admin",
                "last_name": "User",
                "email": "admin@trileet.com",
                "new_password": "Sesame765",
                "current_password": "",
            },
            form_errors={"current_password": "Please enter your password to save changes."},
            object_unchanged=self.admin,
        )

        # try to change password to something too simple
        self.assertUpdateSubmit(
            edit_url,
            self.admin,
            {
                "language": "en-us",
                "first_name": "Admin",
                "last_name": "User",
                "email": "admin@trileet.com",
                "new_password": "123",
                "current_password": "Qwerty123",
            },
            form_errors={"new_password": "This password is too short. It must contain at least 8 characters."},
            object_unchanged=self.admin,
        )

        # submit with current password
        self.assertUpdateSubmit(
            edit_url,
            self.admin,
            {
                "language": "en-us",
                "first_name": "Admin",
                "last_name": "User",
                "email": "admin@trileet.com",
                "new_password": "Sesame765",
                "current_password": "Qwerty123",
            },
        )

        # should have a password changed notification
        self.assertEqual(
            {"user:email", "user:password"}, set(self.admin.notifications.values_list("notification_type", flat=True))
        )

        # check that user still has a valid session
        self.assertEqual(200, self.client.get(reverse("msgs.msg_inbox")).status_code)

        # reset password as test suite assumes this password
        self.admin.set_password("Qwerty123")
        self.admin.save()

        # submit when language isn't an option
        with override_settings(LANGUAGES=(("en-us", "English"),)):
            self.assertUpdateSubmit(
                edit_url,
                self.admin,
                {
                    "first_name": "Andy",
                    "last_name": "Flows",
                    "email": "admin@trileet.com",
                },
            )

            self.admin.refresh_from_db()
            self.admin.settings.refresh_from_db()
            self.assertEqual("Andy", self.admin.first_name)
            self.assertEqual("en-us", self.admin.settings.language)

    def test_forget(self):
        forget_url = reverse("orgs.user_forget")

        # make sure smartmin view is redirecting to our view
        response = self.client.get(reverse("users.user_forget"))
        self.assertRedirect(response, forget_url, status_code=301)

        FailedLogin.objects.create(username="admin@nyaruka.com")
        invitation = Invitation.objects.create(
            org=self.org,
            user_group="A",
            email="invited@nyaruka.com",
            created_by=self.admin,
            modified_by=self.admin,
        )

        # no login required to access
        response = self.client.get(forget_url)
        self.assertEqual(200, response.status_code)

        # try submitting email addess that don't exist in the system
        response = self.client.post(forget_url, {"email": "foo@nyaruka.com"})
        self.assertLoginRedirect(response)
        self.assertEqual(0, len(mail.outbox))  # no emails sent

        # try submitting email address that has been invited
        response = self.client.post(forget_url, {"email": "invited@nyaruka.com"})
        self.assertLoginRedirect(response)

        # invitation email should have been resent
        self.assertEqual(1, len(mail.outbox))
        self.assertEqual(["invited@nyaruka.com"], mail.outbox[0].recipients())
        self.assertIn(invitation.secret, mail.outbox[0].body)

        # try submitting email address for existing user
        response = self.client.post(forget_url, {"email": "admin@nyaruka.com"})
        self.assertLoginRedirect(response)

        # will have a recovery token
        token1 = RecoveryToken.objects.get(user=self.admin)

        # and a recovery link email sent
        self.assertEqual(2, len(mail.outbox))
        self.assertEqual(["admin@nyaruka.com"], mail.outbox[1].recipients())
        self.assertIn(token1.token, mail.outbox[1].body)

        # try submitting again for same email address - should error because it's too soon after last one
        response = self.client.post(forget_url, {"email": "admin@nyaruka.com"})
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "A recovery email was already sent to this address recently.")

        # make that token look older and try again
        token1.created_on = timezone.now() - timedelta(minutes=30)
        token1.save(update_fields=("created_on",))

        response = self.client.post(forget_url, {"email": "admin@nyaruka.com"})
        self.assertLoginRedirect(response)

        # will have a new recovery token and the previous one is deleted
        token2 = RecoveryToken.objects.get(user=self.admin)
        self.assertFalse(RecoveryToken.objects.filter(id=token1.id).exists())

        self.assertEqual(3, len(mail.outbox))
        self.assertEqual(["admin@nyaruka.com"], mail.outbox[2].recipients())
        self.assertIn(token2.token, mail.outbox[2].body)

        # failed login records unaffected
        self.assertEqual(1, FailedLogin.objects.filter(username="admin@nyaruka.com").count())

    def test_recover(self):
        recover_url = reverse("orgs.user_recover", args=["1234567890"])

        FailedLogin.objects.create(username="admin@nyaruka.com")
        FailedLogin.objects.create(username="editor@nyaruka.com")

        # make sure smartmin view is redirecting to our view
        response = self.client.get(reverse("users.user_recover", args=["1234567890"]))
        self.assertRedirect(response, recover_url, status_code=301)

        # 404 if token doesn't exist
        response = self.client.get(recover_url)
        self.assertEqual(404, response.status_code)

        # create token but too old
        token = RecoveryToken.objects.create(
            user=self.admin, token="1234567890", created_on=timezone.now() - timedelta(days=1)
        )

        # user will be redirected to forget password page and told to start again
        response = self.client.get(recover_url)
        self.assertRedirect(response, reverse("orgs.user_forget"))

        token.created_on = timezone.now() - timedelta(minutes=45)
        token.save(update_fields=("created_on",))

        self.assertUpdateFetch(recover_url, [None], form_fields=("new_password", "confirm_password"))

        # try submitting empty form
        self.assertUpdateSubmit(
            recover_url,
            None,
            {},
            form_errors={"new_password": "This field is required.", "confirm_password": "This field is required."},
            object_unchanged=self.admin,
        )

        # try to set password to something too simple
        self.assertUpdateSubmit(
            recover_url,
            None,
            {"new_password": "123", "confirm_password": "123"},
            form_errors={"new_password": "This password is too short. It must contain at least 8 characters."},
            object_unchanged=self.admin,
        )

        # try to set password but confirmation doesn't match
        self.assertUpdateSubmit(
            recover_url,
            None,
            {"new_password": "Qwerty123", "confirm_password": "Azerty123"},
            form_errors={"__all__": "New password and confirmation don't match."},
            object_unchanged=self.admin,
        )

        # on successfull password reset, user is redirected to login page
        response = self.assertUpdateSubmit(
            recover_url, None, {"new_password": "Azerty123", "confirm_password": "Azerty123"}
        )
        self.assertLoginRedirect(response)

        response = self.client.get(response.url)
        self.assertContains(response, "Your password has been updated successfully.")

        # their password has been updated, recovery token deleted and any failed login records deleted
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("Azerty123"))
        self.assertEqual(0, self.admin.recovery_tokens.count())

        self.assertEqual(0, FailedLogin.objects.filter(username="admin@nyaruka.com").count())  # deleted
        self.assertEqual(1, FailedLogin.objects.filter(username="editor@nyaruka.com").count())  # unaffected

    def test_token(self):
        token_url = reverse("orgs.user_token")

        self.assertRequestDisallowed(token_url, [None, self.user, self.agent])

        self.login(self.editor)

        editor_token = self.editor.get_api_token(self.org)

        response = self.client.get(token_url)
        self.assertContains(response, editor_token)

        # a post should refresh the token
        response = self.client.post(token_url, {}, follow=True)
        self.assertNotContains(response, editor_token)

        new_token = self.editor.get_api_token(self.org)

        self.assertContains(response, new_token)

    def test_verify_email(self):
        self.assertEqual(self.admin.settings.email_status, "U")
        self.assertTrue(self.admin.settings.email_verification_secret)

        self.admin.settings.email_verification_secret = "SECRET"
        self.admin.settings.save(update_fields=("email_verification_secret",))

        verify_url = reverse("orgs.user_verify_email", args=["SECRET"])

        # try to access before logging in
        response = self.client.get(verify_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(reverse("orgs.user_verify_email", args=["WRONG_SECRET"]))
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "This email verification link is invalid.")

        response = self.client.get(verify_url, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "verified successfully")
        self.assertContains(response, reverse("orgs.org_start"))

        self.admin.settings.refresh_from_db()
        self.assertEqual(self.admin.settings.email_status, "V")

        # use the same link again
        response = self.client.get(verify_url)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "verified successfully")
        self.assertContains(response, reverse("orgs.org_start"))

        self.login(self.admin2)
        self.assertEqual(self.admin2.settings.email_status, "U")

        # user is told to login as that user
        response = self.client.get(verify_url)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "This email verification link is for a different user.")
        self.assertContains(response, reverse("users.user_login"))

        # and isn't verified
        self.admin2.settings.refresh_from_db()
        self.assertEqual(self.admin2.settings.email_status, "U")

    def test_send_verification_email(self):
        r = get_redis_connection()
        send_verification_email_url = reverse("orgs.user_send_verification_email")

        # try to access before logging in
        response = self.client.get(send_verification_email_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(send_verification_email_url)
        self.assertEqual(405, response.status_code)

        key = f"send_verification_email:{self.admin.email}".lower()

        # simulate haivng the redis key already set
        r.set(key, "1", ex=60 * 10)

        response = self.client.post(send_verification_email_url, {}, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertToast(response, "info", "Verification email already sent. You can retry in 10 minutes.")
        self.assertEqual(0, len(mail.outbox))

        # no email when the redis key is set even with the task itself
        send_user_verification_email.delay(self.org.id, self.admin.id)
        self.assertEqual(0, len(mail.outbox))

        # remove the redis key, as the key expired
        r.delete(key)

        response = self.client.post(send_verification_email_url, {}, follow=True)
        self.assertEqual(200, response.status_code)
        self.assertToast(response, "info", "Verification email sent")

        # and one email sent
        self.assertEqual(1, len(mail.outbox))

        self.admin.settings.email_status = "V"
        self.admin.settings.save(update_fields=("email_status",))

        response = self.client.post(send_verification_email_url, {}, follow=True)
        self.assertEqual(200, response.status_code)

        # no new email sent
        self.assertEqual(1, len(mail.outbox))

        # even the method will not send the email for verified status
        send_user_verification_email.delay(self.org.id, self.admin.id)

        # no new email sent
        self.assertEqual(1, len(mail.outbox))


class BulkExportTest(TembaTest):

    def _export(self, flows=[], campaigns=[]):
        export = DefinitionExport.create(self.org, self.admin, flows=flows, campaigns=campaigns)
        export.perform()

        filename = f"{settings.MEDIA_ROOT}/test_orgs/{self.org.id}/definition_exports/{export.uuid}.json"

        with open(filename) as export_file:
            definitions = json.loads(export_file.read())

        return definitions, export

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

        exported, export_obj = self._export(flows=[parent], campaigns=[])

        # shouldn't have any triggers
        self.assertFalse(exported["triggers"])

    def test_subflow_dependencies(self):
        self.import_file("subflow")

        parent = Flow.objects.filter(name="Parent Flow").first()
        child = Flow.objects.filter(name="Child Flow").first()
        self.assertIn(child, parent.flow_dependencies.all())

        self.login(self.admin)
        response = self.client.get(reverse("orgs.org_export"))

        self.assertEqual(1, len(response.context["buckets"]))
        self.assertEqual([child, parent], response.context["buckets"][0])

    def test_import_voice_flows_expiration_time(self):
        # import file has invalid expires for an IVR flow so it should get the default (5)
        self.get_flow("ivr")

        self.assertEqual(Flow.objects.filter(flow_type=Flow.TYPE_VOICE).count(), 1)
        voice_flow = Flow.objects.get(flow_type=Flow.TYPE_VOICE)
        self.assertEqual(voice_flow.name, "IVR Flow")
        self.assertEqual(voice_flow.expires_after_minutes, 5)

    def test_import(self):
        self.login(self.admin)

        OrgImport.objects.all().delete()

        post_data = dict(file=open("%s/test_flows/too_old.json" % settings.MEDIA_ROOT, "rb"))
        response = self.client.post(reverse("orgs.orgimport_create"), post_data)
        self.assertFormError(
            response.context["form"], "file", "This file is no longer valid. Please export a new version and try again."
        )

        # try a file which can be migrated forwards
        response = self.client.post(
            reverse("orgs.orgimport_create"),
            {"file": open("%s/test_flows/favorites_v4.json" % settings.MEDIA_ROOT, "rb")},
        )
        self.assertEqual(302, response.status_code)

        # should have created an org import object
        self.assertTrue(OrgImport.objects.filter(org=self.org))

        org_import = OrgImport.objects.filter(org=self.org).get()
        self.assertEqual(org_import.status, OrgImport.STATUS_COMPLETE)

        response = self.client.get(reverse("orgs.orgimport_read", args=(org_import.id,)))
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Finished successfully")

        flow = self.org.flows.filter(name="Favorites").get()
        self.assertEqual(Flow.CURRENT_SPEC_VERSION, flow.version_number)

        # test import using data that is not parsable
        junk_binary_data = io.BytesIO(b"\x00!\x00b\xee\x9dh^\x01\x00\x00\x04\x00\x02[Content_Types].xml \xa2\x04\x02(")
        post_data = dict(file=junk_binary_data)
        response = self.client.post(reverse("orgs.orgimport_create"), post_data)
        self.assertFormError(response.context["form"], "file", "This file is not a valid flow definition file.")

        junk_json_data = io.BytesIO(b'{"key": "data')
        post_data = dict(file=junk_json_data)
        response = self.client.post(reverse("orgs.orgimport_create"), post_data)
        self.assertFormError(response.context["form"], "file", "This file is not a valid flow definition file.")

    def test_import_errors(self):
        self.login(self.admin)
        OrgImport.objects.all().delete()

        # simulate an unexpected exception during import
        with patch("temba.triggers.models.Trigger.import_triggers") as validate:
            validate.side_effect = Exception("Unexpected Error")
            post_data = dict(file=open("%s/test_flows/new_mother.json" % settings.MEDIA_ROOT, "rb"))
            self.client.post(reverse("orgs.orgimport_create"), post_data)

            org_import = OrgImport.objects.filter(org=self.org).last()
            self.assertEqual(org_import.status, OrgImport.STATUS_FAILED)

            # trigger import failed, new flows that were added should get rolled back
            self.assertIsNone(Flow.objects.filter(org=self.org, name="New Mother").first())

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

    @mock_mailroom
    def test_import_flow_issues(self, mr_mocks):
        # first call is during import to find dependencies to map or create
        mr_mocks.flow_inspect(dependencies=[{"key": "age", "name": "", "type": "field", "missing": False}])

        # second call is in save_revision and passes org to validate dependencies, but during import those
        # dependencies which didn't exist already are created in a transaction and mailroom can't see them
        mr_mocks.flow_inspect(
            dependencies=[{"key": "age", "name": "", "type": "field", "missing": True}],
            issues=[{"type": "missing_dependency"}],
        )

        # final call is after new flows and dependencies have been committed so mailroom can see them
        mr_mocks.flow_inspect(dependencies=[{"key": "age", "name": "", "type": "field", "missing": False}])

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
        flow_info = mailroom.get_client().flow_inspect(self.org, definition)
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

        mr_mocks.contact_parse_query("facts_per_day = 1", fields=["facts_per_day"])
        mr_mocks.contact_parse_query("likes_cats = true", cleaned='likes_cats = "true"', fields=["likes_cats"])

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

        mr_mocks.contact_parse_query("facts_per_day = 1", fields=["facts_per_day"])
        mr_mocks.contact_parse_query("likes_cats = true", cleaned='likes_cats = "true"', fields=["likes_cats"])

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
            self.org,
            self.admin,
            Trigger.TYPE_KEYWORD,
            flow1,
            keywords=["rating"],
            match_type=Trigger.MATCH_FIRST_WORD,
            is_archived=True,
        )
        trigger2 = Trigger.create(
            self.org, self.admin, Trigger.TYPE_KEYWORD, flow2, keywords=["rating"], match_type=Trigger.MATCH_FIRST_WORD
        )

        data = self.get_import_json("rating_10")

        self.org.import_app(data, self.admin, site="http://rapidpro.io")

        # trigger1.refresh_from_db()
        # self.assertFalse(trigger1.is_archived)

        flow = Flow.objects.get(name="Rate us")
        self.assertEqual(1, Trigger.objects.filter(keywords=["rating"], is_archived=False).count())
        self.assertEqual(1, Trigger.objects.filter(flow=flow).count())

        # shoud have archived the existing
        self.assertFalse(Trigger.objects.filter(id=trigger1.id, is_archived=False).first())
        self.assertFalse(Trigger.objects.filter(id=trigger2.id, is_archived=False).first())

        # Archive trigger
        flow_trigger = (
            Trigger.objects.filter(flow=flow, keywords=["rating"], is_archived=False).order_by("-created_on").first()
        )
        flow_trigger.archive(self.admin)

        # re import again will restore the trigger
        data = self.get_import_json("rating_10")
        self.org.import_app(data, self.admin, site="http://rapidpro.io")

        flow_trigger.refresh_from_db()

        self.assertEqual(1, Trigger.objects.filter(keywords=["rating"], is_archived=False).count())
        self.assertEqual(1, Trigger.objects.filter(flow=flow).count())
        self.assertFalse(Trigger.objects.filter(pk=trigger1.pk, is_archived=False).first())
        self.assertFalse(Trigger.objects.filter(pk=trigger2.pk, is_archived=False).first())

        restored_trigger = (
            Trigger.objects.filter(flow=flow, keywords=["rating"], is_archived=False).order_by("-created_on").first()
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

        trigger = Trigger.objects.filter(keywords=["patient"]).first()
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
        trigger = Trigger.objects.filter(keywords=["patient"]).order_by("-created_on").first()
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

        response = self.client.post(reverse("orgs.org_export"), post_data, follow=True)

        self.assertEqual(1, Export.objects.count())

        export = Export.objects.get()
        self.assertEqual("definition", export.export_type)

        flows = Flow.objects.filter(flow_type="M", is_system=False)
        campaigns = Campaign.objects.all()

        exported, export_obj = self._export(flows=flows, campaigns=campaigns)

        response = self.client.get(reverse("orgs.export_download", args=[export_obj.uuid]))
        self.assertEqual(response.status_code, 200)

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

        # make sure the base language is set to 'und', not 'eng'
        self.assertEqual(message_flow.base_language, "und")

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

    def test_prevent_flow_type_changes(self):
        flow1 = self.get_flow("favorites")
        flow1.name = "Background"
        flow1.save(update_fields=("name",))

        flow2 = self.get_flow("background")  # contains a flow called Background

        flow1.refresh_from_db()
        flow2.refresh_from_db()

        self.assertNotEqual(flow1, flow2)
        self.assertEqual("M", flow1.flow_type)
        self.assertEqual("B", flow2.flow_type)
        self.assertEqual("Background 2", flow2.name)


class InvitationCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_create(self):
        create_url = reverse("orgs.invitation_create")

        self.org.features = [Org.FEATURE_CHILD_ORGS, Org.FEATURE_USERS]
        self.org.save(update_fields=("features",))

        self.assertRequestDisallowed(create_url, [None, self.user, self.agent, self.editor])
        self.assertCreateFetch(create_url, [self.admin], form_fields={"email": None, "role": "E"})

        # try submitting without email
        self.assertCreateSubmit(
            create_url, self.admin, {"email": "", "role": "E"}, form_errors={"email": "This field is required."}
        )

        # try submitting with invalid email
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"email": "@@@@", "role": "E"},
            form_errors={"email": "Enter a valid email address."},
        )

        # try submitting the email of an existing user (check is case-insensitive)
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"email": "EDITOR@nyaruka.com", "role": "E"},
            form_errors={"email": "User is already a member of this workspace."},
        )

        # submit with valid email
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"email": "newguy@nyaruka.com", "role": "A"},
            new_obj_query=Invitation.objects.filter(org=self.org, email="newguy@nyaruka.com", user_group="A").exclude(
                secret=None
            ),
        )

        # check invitation email has been sent
        self.assertEqual(1, len(mail.outbox))

        # try submitting for same email again
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"email": "newguy@nyaruka.com", "role": "E"},
            form_errors={"email": "User has already been invited to this workspace."},
        )

        # view can create invitations in child orgs
        child1 = self.org.create_new(self.admin, "Child 1", tzone.utc, as_child=True)
        child1.features = [Org.FEATURE_USERS]
        child1.save(update_fields=("features",))

        self.org.create_new(self.admin, "Child 2", tzone.utc, as_child=True)

        self.assertCreateSubmit(
            create_url + f"?org={child1.id}",
            self.admin,
            {"email": "newguy@nyaruka.com", "role": "A"},
            new_obj_query=Invitation.objects.filter(org=child1, email="newguy@nyaruka.com", user_group="A").exclude(
                secret=None
            ),
        )
        self.assertEqual(2, len(mail.outbox))

        # view can't create invitations in other orgs
        response = self.client.get(create_url + f"?org={self.org2.id}")
        self.assertEqual(404, response.status_code)


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


class ExportTest(TembaTest):
    def test_trim_task(self):
        export1 = TicketExport.create(
            self.org, self.admin, start_date=date.today() - timedelta(days=7), end_date=date.today(), with_fields=()
        )
        export2 = TicketExport.create(
            self.org, self.admin, start_date=date.today() - timedelta(days=7), end_date=date.today(), with_fields=()
        )
        export1.perform()
        export2.perform()

        self.assertTrue(default_storage.exists(export1.path))
        self.assertTrue(default_storage.exists(export2.path))

        # make export 1 look old
        export1.created_on = timezone.now() - timedelta(days=100)
        export1.save(update_fields=("created_on",))

        trim_exports()

        self.assertFalse(Export.objects.filter(id=export1.id).exists())
        self.assertTrue(Export.objects.filter(id=export2.id).exists())

        self.assertFalse(default_storage.exists(export1.path))
        self.assertTrue(default_storage.exists(export2.path))


class ExportCRUDLTest(TembaTest):
    def test_download(self):
        export = TicketExport.create(
            self.org, self.admin, start_date=date.today() - timedelta(days=7), end_date=date.today(), with_fields=()
        )
        export.perform()

        self.assertEqual(1, self.admin.notifications.filter(notification_type="export:finished", is_seen=False).count())

        download_url = reverse("orgs.export_download", kwargs={"uuid": export.uuid})

        self.assertEqual(f"/export/download/{export.uuid}/", download_url)
        self.assertEqual(
            (
                f"/media/test_orgs/{self.org.id}/ticket_exports/{export.uuid}.xlsx",
                f"tickets_{datetime.today().strftime(r'%Y%m%d')}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            export.get_raw_access(),
        )

        response = self.client.get(download_url)
        self.assertLoginRedirect(response)

        # user who didn't create the export and access it...
        self.login(self.editor)
        response = self.client.get(download_url)

        # which doesn't affect admin's notification
        self.assertEqual(1, self.admin.notifications.filter(notification_type="export:finished", is_seen=False).count())

        # but them accessing it will
        self.login(self.admin)
        response = self.client.get(download_url)

        self.assertEqual(0, self.admin.notifications.filter(notification_type="export:finished", is_seen=False).count())

        response = self.client.get(download_url + "?raw=1")
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", response.headers["content-type"]
        )
