import io
from datetime import date, datetime, timedelta, timezone as tzone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from smartmin.users.models import FailedLogin

from django.conf import settings
from django.contrib.auth.models import Group
from django.core import mail
from django.core.files.storage import default_storage
from django.db.models import F, Model
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba import mailroom
from temba.api.models import APIToken, Resthook, WebHookEvent
from temba.archives.models import Archive
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import ChannelLog, SyncEvent
from temba.classifiers.models import Classifier
from temba.classifiers.types.wit import WitType
from temba.contacts.models import ContactExport, ContactField, ContactGroup, ContactImport, ContactImportBatch
from temba.flows.models import Flow, FlowLabel, FlowRun, FlowSession, FlowStart, FlowStartCount, ResultsExport
from temba.globals.models import Global
from temba.locations.models import AdminBoundary
from temba.msgs.models import Label, MessageExport, Msg
from temba.notifications.incidents.builtin import ChannelDisconnectedIncidentType
from temba.notifications.types.builtin import ExportFinishedNotificationType
from temba.request_logs.models import HTTPLog
from temba.schedules.models import Schedule
from temba.templates.models import TemplateTranslation
from temba.tests import TembaTest, matchers, mock_mailroom
from temba.tests.base import get_contact_search
from temba.tickets.models import Team, TicketExport, Topic
from temba.triggers.models import Trigger
from temba.utils import json
from temba.utils.uuid import uuid4

from .models import (
    BackupToken,
    DefinitionExport,
    Export,
    Invitation,
    ItemCount,
    Org,
    OrgImport,
    OrgMembership,
    OrgRole,
    User,
    UserSettings,
)
from .tasks import delete_released_orgs, expire_invitations, restart_stalled_exports, squash_item_counts, trim_exports


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


class InvitationTest(TembaTest):
    def test_model(self):
        invitation = Invitation.create(self.org, self.admin, "invitededitor@textit.com", OrgRole.EDITOR)

        self.assertEqual(OrgRole.EDITOR, invitation.role)

        invitation.send()

        self.assertEqual(1, len(mail.outbox))
        self.assertEqual(["invitededitor@textit.com"], mail.outbox[0].recipients())
        self.assertEqual("RapidPro Invitation", mail.outbox[0].subject)
        self.assertIn(f"https://app.rapidpro.io/org/join/{invitation.secret}/", mail.outbox[0].body)

        new_editor = User.create("invitededitor@textit.com", "Bob", "", "Qwerty123", "en-US")
        invitation.accept(new_editor)

        self.assertEqual(1, self.admin.notifications.count())
        self.assertFalse(invitation.is_active)
        self.assertEqual({self.editor, new_editor}, set(self.org.get_users(roles=[OrgRole.EDITOR])))

        # invite an agent user to a specific team
        sales = Team.create(self.org, self.admin, "Sales", topics=[])
        invitation = Invitation.create(self.org, self.admin, "invitedagent@textit.com", OrgRole.AGENT, team=sales)

        self.assertEqual(OrgRole.AGENT, invitation.role)
        self.assertEqual(sales, invitation.team)

        invitation.send()
        new_agent = User.create("invitedagent@textit.com", "Bob", "", "Qwerty123", "en-US")
        invitation.accept(new_agent)

        self.assertEqual({self.agent, new_agent}, set(self.org.get_users(roles=[OrgRole.AGENT])))
        self.assertEqual({new_agent}, set(sales.get_users()))

    def test_expire_task(self):
        invitation1 = Invitation.objects.create(
            org=self.org,
            role_code="E",
            email="neweditor@textit.com",
            created_by=self.admin,
            created_on=timezone.now() - timedelta(days=31),
            modified_by=self.admin,
        )
        invitation2 = Invitation.objects.create(
            org=self.org,
            role_code="T",
            email="newagent@textit.com",
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
                {self.agent: False, self.user: True, self.admin: True, self.admin2: False},
            ),
            (
                self.org2,
                "contacts.contact_list",
                {self.agent: False, self.user: False, self.admin: False, self.admin2: True},
            ),
            (
                self.org2,
                "contacts.contact_read",
                {self.agent: False, self.user: False, self.admin: False, self.admin2: True},
            ),
            (
                self.org,
                "orgs.org_edit",
                {self.agent: False, self.user: False, self.admin: True, self.admin2: False},
            ),
            (
                self.org2,
                "orgs.org_edit",
                {self.agent: False, self.user: False, self.admin: False, self.admin2: True},
            ),
            (
                self.org,
                "orgs.org_grant",
                {self.agent: False, self.user: False, self.admin: False, self.admin2: False, granter: True},
            ),
            (
                self.org,
                "xxx.yyy_zzz",
                {self.agent: False, self.user: False, self.admin: False, self.admin2: False},
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
        login_url = reverse("orgs.user_login")
        verify_url = reverse("orgs.two_factor_verify")
        backup_url = reverse("orgs.two_factor_backup")

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
        response = self.client.post(login_url, {"username": "admin@textit.com", "password": "Qwerty123"})
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
            login_url + "?next=/msg/", {"username": "admin@textit.com", "password": "Qwerty123"}
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
        response = self.client.post(login_url, {"username": "admin@textit.com", "password": "Qwerty123"})
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
        login_url = reverse("orgs.user_login")
        verify_url = reverse("orgs.two_factor_verify")
        backup_url = reverse("orgs.two_factor_backup")
        failed_url = reverse("users.user_failed")

        # submit incorrect username and password 3 times
        self.client.post(login_url, {"username": "admin@textit.com", "password": "pass123"})
        self.client.post(login_url, {"username": "admin@textit.com", "password": "pass123"})
        response = self.client.post(login_url, {"username": "admin@textit.com", "password": "pass123"})

        self.assertRedirect(response, failed_url)
        self.assertRedirect(self.client.get(reverse("msgs.msg_inbox")), login_url)

        # simulate failed logins timing out by making them older
        FailedLogin.objects.all().update(failed_on=timezone.now() - timedelta(minutes=3))

        # now we're allowed to make failed logins again
        response = self.client.post(login_url, {"username": "admin@textit.com", "password": "pass123"})
        self.assertFormError(
            response.context["form"],
            None,
            "Please enter a correct username and password. Note that both fields may be case-sensitive.",
        )

        # and successful logins
        response = self.client.post(login_url, {"username": "admin@textit.com", "password": "Qwerty123"})
        self.assertRedirect(response, reverse("orgs.org_choose"))

        # try again with 2FA enabled
        self.client.logout()
        self.admin.enable_2fa()

        # submit incorrect username and password 3 times
        self.client.post(login_url, {"username": "admin@textit.com", "password": "pass123"})
        self.client.post(login_url, {"username": "admin@textit.com", "password": "pass123"})
        response = self.client.post(login_url, {"username": "admin@textit.com", "password": "pass123"})

        self.assertRedirect(response, failed_url)
        self.assertRedirect(self.client.get(reverse("msgs.msg_inbox")), login_url)

        # login correctly
        FailedLogin.objects.all().delete()
        response = self.client.post(login_url, {"username": "admin@textit.com", "password": "Qwerty123"})
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

        response = self.client.post(login_url, {"username": "admin@textit.com", "password": "Qwerty123"})
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
        login_url = reverse("orgs.user_login")
        verify_url = reverse("orgs.two_factor_verify")
        backup_url = reverse("orgs.two_factor_backup")

        self.admin.enable_2fa()

        # simulate a login for a 2FA user 10 minutes ago
        with patch("django.utils.timezone.now", return_value=timezone.now() - timedelta(minutes=10)):
            response = self.client.post(login_url, {"username": "admin@textit.com", "password": "Qwerty123"})
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
        confirm_url = reverse("orgs.confirm_access") + "?next=/msg/"
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

    @mock_mailroom
    def test_release(self, mr_mocks):
        token = APIToken.create(self.org, self.admin)

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

        token.refresh_from_db()
        self.assertFalse(token.is_active)


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
        admin3 = self.create_user("bob@textit.com")

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
        self.other_admin = self.create_user("other_admin@textit.com")
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
        topic = add(Topic.create(org, user, "Spam"))
        ticket1 = add(self.create_ticket(contacts[0], topic))
        ticket1.events.create(org=org, contact=contacts[0], event_type="N", note="spam", created_by=user)

        add(self.create_ticket(contacts[0], opened_in=flows[0]))
        add(Team.create(org, user, "Spam Only", topics=[topic]))

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
        daily = add(self.create_archive(Archive.TYPE_MSG, Archive.PERIOD_DAILY, timezone.now(), [{"id": 1}], org=org))
        add(
            self.create_archive(
                Archive.TYPE_MSG, Archive.PERIOD_MONTHLY, timezone.now(), [{"id": 1}], rollup_of=(daily,), org=org
            )
        )

        # extra S3 file in archive dir
        Archive.storage().save(f"{org.id}/extra_file.json", io.StringIO("[]"))

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
        Org.objects.exclude(released_on=None).update(released_on=F("released_on") - timedelta(days=8))

        delete_released_orgs()

        self.assertOrgDeleted(self.org, org1_content)
        self.assertOrgDeleted(org1_child1)
        self.assertOrgDeleted(org1_child2)
        self.assertOrgActive(self.org2, org2_content)

        # only org 2 files left in S3
        for archive in self.org2.archives.all():
            self.assertTrue(Archive.storage().exists(archive.get_storage_location()[1]))

        self.assertTrue(Archive.storage().exists(f"{self.org2.id}/extra_file.json"))
        self.assertFalse(Archive.storage().exists(f"{self.org.id}/extra_file.json"))

        # check we've initiated search de-indexing for all deleted orgs
        self.assertEqual({org1_child1, org1_child2, self.org}, {c.args[0] for c in mr_mocks.calls["org_deindex"]})

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


class BulkExportTest(TembaTest):

    def _export(self, flows=[], campaigns=[]):
        export = DefinitionExport.create(self.org, self.admin, flows=flows, campaigns=campaigns)
        export.perform()

        with default_storage.open(f"orgs/{self.org.id}/definition_exports/{export.uuid}.json") as export_file:
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
        self.import_file("test_flows/parent_child_trigger.json")

        parent = Flow.objects.filter(name="Parent Flow").first()

        self.login(self.admin)

        exported, export_obj = self._export(flows=[parent], campaigns=[])

        # shouldn't have any triggers
        self.assertFalse(exported["triggers"])

    def test_subflow_dependencies(self):
        self.import_file("test_flows/subflow.json")

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
        self.import_file("test_flows/campaign_import_with_translations.json")

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
        self.import_file("test_flows/survey_campaign.json")

        campaign = Campaign.objects.filter(is_active=True).last()
        event = campaign.events.filter(is_active=True).last()

        # create a contact and place her into our campaign
        sally = self.create_contact("Sally", phone="+12345", fields={"survey_start": "10-05-2025 12:30:10"})
        campaign.group.contacts.add(sally)

        # importing it again shouldn't result in failures
        self.import_file("test_flows/survey_campaign.json")

        # get our latest campaign and event
        new_campaign = Campaign.objects.filter(is_active=True).last()
        new_event = campaign.events.filter(is_active=True).last()

        # same campaign, but new event
        self.assertEqual(campaign.id, new_campaign.id)
        self.assertNotEqual(event.id, new_event.id)

    def test_import_mixed_flow_versions(self):
        self.import_file("test_flows/mixed_versions.json")

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
        self.import_file("test_flows/all_dependency_types.json")

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

        self.import_file("test_flows/color.json")

        flow = Flow.objects.get()

        self.assertFalse(flow.has_issues)

    def test_import_missing_flow_dependency(self):
        # in production this would blow up validating the flow but we can't do that during tests
        self.import_file("test_flows/parent_without_its_child.json")

        parent = Flow.objects.get(name="Single Parent")
        self.assertEqual(set(parent.flow_dependencies.all()), set())

        # create child with that name and re-import
        child1 = Flow.create(self.org, self.admin, "New Child", Flow.TYPE_MESSAGE)

        self.import_file("test_flows/parent_without_its_child.json")
        self.assertEqual(set(parent.flow_dependencies.all()), {child1})

        # create child with that UUID and re-import
        child2 = Flow.create(
            self.org, self.admin, "New Child 2", Flow.TYPE_MESSAGE, uuid="a925453e-ad31-46bd-858a-e01136732181"
        )

        self.import_file("test_flows/parent_without_its_child.json")
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
        data = self.load_json("test_flows/cataclysm.json")

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
        data = self.load_json("test_flows/cataclysm.json")
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

        self.import_file("test_flows/cataclysm.json")

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

        data = self.load_json("test_flows/rating_10.json")

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
        data = self.load_json("test_flows/rating_10.json")
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
        self.import_file("test_flows/the_clinic.json")

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
        self.import_file("test_flows/the_clinic.json")

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


class ItemCountTest(TembaTest):
    def test_model(self):
        self.org.counts.create(scope="foo:1", count=2)
        self.org.counts.create(scope="foo:1", count=3)
        self.org.counts.create(scope="foo:2", count=1)
        self.org.counts.create(scope="foo:3", count=4)
        self.org2.counts.create(scope="foo:4", count=1)
        self.org2.counts.create(scope="foo:4", count=1)

        self.assertEqual(9, ItemCount.sum(self.org.counts.filter(scope__in=("foo:1", "foo:3"))))
        self.assertEqual(10, ItemCount.sum(self.org.counts.filter(scope__startswith="foo:")))
        self.assertEqual(4, self.org.counts.count())

        squash_item_counts()

        self.assertEqual(9, ItemCount.sum(self.org.counts.filter(scope__in=("foo:1", "foo:3"))))
        self.assertEqual(10, ItemCount.sum(self.org.counts.filter(scope__startswith="foo:")))
        self.assertEqual(3, self.org.counts.count())

        self.org.counts.all().delete()
