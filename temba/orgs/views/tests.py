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
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.channels.models import Channel
from temba.contacts.models import URN
from temba.tests import CRUDLTestMixin, TembaTest
from temba.tickets.models import Team, TicketExport
from temba.utils import languages

from ..models import Invitation, Org, OrgRole, User
from ..tasks import send_user_verification_email
from .context_processors import RolePermsWrapper


class OrgPermsMixinTest(TembaTest):
    def test_has_permission(self):
        create_url = reverse("tickets.topic_create")

        # no anon access
        self.assertLoginRedirect(self.client.get(create_url))

        # no agent role access to this specific view
        self.login(self.agent)
        self.assertLoginRedirect(self.client.get(create_url))

        # editor role does have access tho
        self.login(self.editor)
        self.assertEqual(200, self.client.get(create_url).status_code)

        # staff can't access without org
        self.login(self.customer_support)
        self.assertLoginRedirect(self.client.get(create_url))

        self.login(self.customer_support, choose_org=self.org)
        self.assertEqual(200, self.client.get(create_url).status_code)

        # staff still can't POST
        self.assertLoginRedirect(self.client.post(create_url, {"name": "Sales"}))

        # but superuser can
        self.customer_support.is_superuser = True
        self.customer_support.save(update_fields=("is_superuser",))

        self.assertEqual(200, self.client.get(create_url).status_code)
        self.assertRedirect(self.client.post(create_url, {"name": "Sales"}), "hide")

        # however if a staff user also belongs to an org, they aren't limited to GETs
        self.admin.is_staff = True
        self.admin.save(update_fields=("is_staff",))

        self.assertEqual(200, self.client.get(create_url).status_code)
        self.assertRedirect(self.client.post(create_url, {"name": "Support"}), "hide")

    def test_org_obj_perms_mixin(self):
        contact1 = self.create_contact("Bob", phone="+18001234567", org=self.org)
        contact2 = self.create_contact("Zob", phone="+18001234567", org=self.org2)

        contact1_url = reverse("contacts.contact_update", args=[contact1.id])
        contact2_url = reverse("contacts.contact_update", args=[contact2.id])

        # no anon access
        self.assertLoginRedirect(self.client.get(contact1_url))
        self.assertLoginRedirect(self.client.get(contact2_url))

        # no agent role access to this specific view
        self.login(self.agent)
        self.assertLoginRedirect(self.client.get(contact1_url))
        self.assertLoginRedirect(self.client.get(contact2_url))

        # editor role does have access tho.. when the URL is for a group in their org
        self.login(self.editor)
        self.assertEqual(200, self.client.get(contact1_url).status_code)
        self.assertLoginRedirect(self.client.get(contact2_url))

        # staff can't access without org
        self.login(self.customer_support)
        self.assertRedirect(self.client.get(contact1_url), "/staff/org/service/")

        self.login(self.customer_support, choose_org=self.org)
        self.assertEqual(200, self.client.get(contact1_url).status_code)
        self.assertRedirect(self.client.get(contact2_url), "/staff/org/service/")  # wrong org

        # staff still can't POST
        self.assertLoginRedirect(self.client.post(contact1_url, {"name": "Bob"}))
        self.assertRedirect(self.client.get(contact2_url), "/staff/org/service/")


class OrgContextProcessorTest(TembaTest):
    def test_role_perms_wrapper(self):
        perms = RolePermsWrapper(OrgRole.ADMINISTRATOR)

        self.assertTrue(perms["msgs"]["msg_list"])
        self.assertTrue(perms["contacts"]["contact_update"])
        self.assertTrue(perms["orgs"]["org_country"])
        self.assertTrue(perms["orgs"]["user_list"])
        self.assertTrue(perms["orgs"]["org_delete"])

        perms = RolePermsWrapper(OrgRole.EDITOR)

        self.assertTrue(perms["msgs"]["msg_list"])
        self.assertTrue(perms["contacts"]["contact_update"])
        self.assertFalse(perms["orgs"]["user_list"])
        self.assertFalse(perms["orgs"]["org_delete"])

        perms = RolePermsWrapper(OrgRole.VIEWER)

        self.assertTrue(perms["msgs"]["msg_list"])
        self.assertFalse(perms["contacts"]["contact_update"])
        self.assertFalse(perms["orgs"]["user_list"])
        self.assertFalse(perms["orgs"]["org_delete"])

        self.assertFalse(perms["msgs"]["foo"])  # no blow up if perm doesn't exist
        self.assertFalse(perms["chickens"]["foo"])  # or app doesn't exist

        with self.assertRaises(TypeError):
            list(perms)


class OrgCRUDLTest(TembaTest, CRUDLTestMixin):
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

        # enable child workspaces, users and teams
        self.org.features = [Org.FEATURE_USERS, Org.FEATURE_CHILD_ORGS, Org.FEATURE_TEAMS]
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
                "Account",
                "Resthooks",
                "Incidents",
                "Workspaces (2)",
                "Dashboard",
                "Users (4)",
                "Invitations (0)",
                "Teams (1)",
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

        invitation = Invitation.create(self.org, self.admin, "edwin@nyaruka.com", OrgRole.EDITOR)

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

        # invitation with mismatching case email
        invitation2 = Invitation.create(self.org2, self.admin, "eDwin@nyaruka.com", OrgRole.EDITOR)

        join_accept_url = reverse("orgs.org_join_accept", args=[invitation2.secret])
        join_url = reverse("orgs.org_join", args=[invitation2.secret])

        self.login(user)

        response = self.client.get(join_url)
        self.assertRedirect(response, join_accept_url)

        # but only if they're the currently logged in user
        self.login(self.admin)

        response = self.client.get(join_url)
        self.assertContains(response, "Sign in to join the <b>Trileet Inc.</b> workspace")
        self.assertContains(response, f"/users/login/?next={join_accept_url}")

    def test_join_signup(self):
        # if invitation secret is invalid, redirect to root
        response = self.client.get(reverse("orgs.org_join_signup", args=["invalid"]))
        self.assertRedirect(response, reverse("public.public_index"))

        invitation = Invitation.create(self.org, self.admin, "administrator@trileet.com", OrgRole.ADMINISTRATOR)

        join_signup_url = reverse("orgs.org_join_signup", args=[invitation.secret])
        join_url = reverse("orgs.org_join", args=[invitation.secret])

        # if user already exists then we redirect back to join
        response = self.client.get(join_signup_url)
        self.assertRedirect(response, join_url)

        invitation = Invitation.create(self.org, self.admin, "edwin@nyaruka.com", OrgRole.EDITOR)

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

        self.assertEqual(1, self.admin.notifications.filter(notification_type="invitation:accepted").count())
        self.assertEqual(2, self.org.get_users(roles=[OrgRole.EDITOR]).count())

    def test_join_accept(self):
        # only authenticated users can access page
        response = self.client.get(reverse("orgs.org_join_accept", args=["invalid"]))
        self.assertLoginRedirect(response)

        # if invitation secret is invalid, redirect to root
        self.login(self.admin)
        response = self.client.get(reverse("orgs.org_join_accept", args=["invalid"]))
        self.assertRedirect(response, reverse("public.public_index"))

        invitation = Invitation.create(self.org, self.admin, "edwin@nyaruka.com", OrgRole.EDITOR)

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

        self.assertEqual(1, self.admin.notifications.filter(notification_type="invitation:accepted").count())
        self.assertEqual(2, self.org.get_users(roles=[OrgRole.EDITOR]).count())

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

        # of which our user is an administrator
        self.assertIn(user, org.get_admins())

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

        # of which our user is an administrator
        self.assertIn(user, org.get_admins())

        # check default org content was created correctly
        system_fields = set(org.fields.filter(is_system=True).values_list("key", flat=True))
        system_groups = set(org.groups.filter(is_system=True).values_list("name", flat=True))
        sample_flows = set(org.flows.values_list("name", flat=True))

        self.assertEqual({"created_on", "last_seen_on"}, system_fields)
        self.assertEqual({"\\Active", "\\Archived", "\\Blocked", "\\Stopped", "Open Tickets"}, system_groups)
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
        create_url = reverse("orgs.org_create")

        # nobody can access if new orgs feature not enabled
        response = self.requestView(create_url, self.admin)
        self.assertRedirect(response, reverse("orgs.org_workspace"))

        self.org.features = [Org.FEATURE_NEW_ORGS]
        self.org.save(update_fields=("features",))

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
        list_url = reverse("orgs.org_list")
        create_url = reverse("orgs.org_create")

        # nobody can access if child orgs feature not enabled
        response = self.requestView(create_url, self.admin)
        self.assertRedirect(response, reverse("orgs.org_workspace"))

        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        response = self.client.get(list_url)
        self.assertContentMenu(list_url, self.admin, ["New"])

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
        self.assertRedirect(response, "/org/")

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

        response = self.client.post(create_url, {"name": "Child Org", "timezone": "Africa/Nairobi"}, HTTP_X_TEMBA_SPA=1)

        self.assertRedirect(response, reverse("orgs.org_list"))

    def test_list(self):
        list_url = reverse("orgs.org_list")

        # nobody can access if child orgs feature not enabled
        response = self.requestView(list_url, self.admin)
        self.assertRedirect(response, reverse("orgs.org_workspace"))

        # enable child orgs and create some child orgs
        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))
        child1 = self.org.create_new(self.admin, "Child Org 1", self.org.timezone, as_child=True)
        child2 = self.org.create_new(self.admin, "Child Org 2", self.org.timezone, as_child=True)

        response = self.assertListFetch(
            list_url, [self.admin], context_objects=[self.org, child1, child2], choose_org=self.org
        )
        self.assertContains(response, "Child Org 1")
        self.assertContains(response, "Child Org 2")

        # can search by name
        self.assertListFetch(
            list_url + "?search=child", [self.admin], context_objects=[child1, child2], choose_org=self.org
        )

    def test_update(self):
        # enable child orgs and create some child orgs
        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))
        child1 = self.org.create_new(self.admin, "Child Org 1", self.org.timezone, as_child=True)

        update_url = reverse("orgs.org_update", args=[child1.id])

        self.assertRequestDisallowed(update_url, [None, self.user, self.editor, self.agent, self.admin2])
        self.assertUpdateFetch(
            update_url, [self.admin], form_fields=["name", "timezone", "date_format", "language"], choose_org=self.org
        )

        response = self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "New Child Name", "timezone": "Africa/Nairobi", "date_format": "Y", "language": "es"},
        )

        child1.refresh_from_db()
        self.assertEqual("New Child Name", child1.name)
        self.assertEqual("/org/", response.url)

        # if org doesn't exist, 404
        response = self.requestView(reverse("orgs.org_update", args=[3464374]), self.admin, choose_org=self.org)
        self.assertEqual(404, response.status_code)

    def test_delete(self):
        self.org.features = [Org.FEATURE_CHILD_ORGS]
        self.org.save(update_fields=("features",))

        child = self.org.create_new(self.admin, "Child Workspace", self.org.timezone, as_child=True)
        delete_url = reverse("orgs.org_delete", args=[child.id])

        self.assertRequestDisallowed(delete_url, [None, self.user, self.editor, self.agent, self.admin2])
        self.assertDeleteFetch(delete_url, [self.admin], choose_org=self.org)

        # schedule for deletion
        response = self.client.get(delete_url)
        self.assertContains(response, "You are about to delete the workspace <b>Child Workspace</b>")

        # go through with it, redirects to workspaces list page
        response = self.client.post(delete_url)
        self.assertEqual(reverse("orgs.org_list"), response["Temba-Success"])

        child.refresh_from_db()
        self.assertFalse(child.is_active)

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
        self.assertRedirect(self.requestView(start_url, self.customer_support), "/staff/org/")

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
        self.assertRedirect(self.requestView(choose_url, self.customer_support), "/staff/org/")

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

        # nobody can access if users feature not enabled
        response = self.requestView(list_url, self.admin)
        self.assertRedirect(response, reverse("orgs.org_workspace"))

        self.org.features = [Org.FEATURE_USERS]
        self.org.save(update_fields=("features",))

        self.assertRequestDisallowed(list_url, [None, self.user, self.editor, self.agent])

        response = self.assertListFetch(
            list_url, [self.admin], context_objects=[self.admin, self.agent, self.editor, self.user]
        )
        self.assertNotContains(response, "(All Topics)")

        self.org.features += [Org.FEATURE_TEAMS]
        self.org.save(update_fields=("features",))

        response = self.assertListFetch(
            list_url, [self.admin], context_objects=[self.admin, self.agent, self.editor, self.user]
        )
        self.assertContains(response, "(All Topics)")

        # can search by name or email
        self.assertListFetch(list_url + "?search=andy", [self.admin], context_objects=[self.admin])
        self.assertListFetch(list_url + "?search=editor@nyaruka.com", [self.admin], context_objects=[self.editor])

    def test_team(self):
        team_url = reverse("orgs.user_team", args=[self.org.default_ticket_team.id])

        # nobody can access if teams feature not enabled
        response = self.requestView(team_url, self.admin)
        self.assertRedirect(response, reverse("orgs.org_workspace"))

        self.org.features = [Org.FEATURE_TEAMS]
        self.org.save(update_fields=("features",))

        self.assertRequestDisallowed(team_url, [None, self.user, self.editor, self.agent])

        self.assertListFetch(team_url, [self.admin], context_objects=[self.agent])
        self.assertContentMenu(team_url, self.admin, [])  # because it's a system team

        team = Team.create(self.org, self.admin, "My Team")
        team_url = reverse("orgs.user_team", args=[team.id])

        self.assertContentMenu(team_url, self.admin, ["Edit", "Delete"])

    def test_update(self):
        update_url = reverse("orgs.user_update", args=[self.agent.id])

        # nobody can access if users feature not enabled
        response = self.requestView(update_url, self.admin)
        self.assertRedirect(response, reverse("orgs.org_workspace"))

        self.org.features = [Org.FEATURE_USERS]
        self.org.save(update_fields=("features",))

        self.assertRequestDisallowed(update_url, [None, self.user, self.editor, self.agent])

        self.assertUpdateFetch(update_url, [self.admin], form_fields={"role": "T"})

        # check can't update user not in the current org
        self.assertRequestDisallowed(reverse("orgs.user_update", args=[self.admin2.id]), [self.admin])

        # role field for viewers defaults to editor
        update_url = reverse("orgs.user_update", args=[self.user.id])

        self.assertUpdateFetch(update_url, [self.admin], form_fields={"role": "E"})

        response = self.assertUpdateSubmit(update_url, self.admin, {"role": "E"})
        self.assertRedirect(response, reverse("orgs.user_list"))

        self.assertEqual({self.user, self.editor}, set(self.org.get_users(roles=[OrgRole.EDITOR])))

        # adding teams feature enables team selection for agents
        self.org.features += [Org.FEATURE_TEAMS]
        self.org.save(update_fields=("features",))
        sales = Team.create(self.org, self.admin, "Sales", topics=[])

        update_url = reverse("orgs.user_update", args=[self.agent.id])

        self.assertUpdateFetch(
            update_url, [self.admin], form_fields={"role": "T", "team": self.org.default_ticket_team}
        )
        self.assertUpdateSubmit(update_url, self.admin, {"role": "T", "team": sales.id})

        self.org._membership_cache = {}
        self.assertEqual(sales, self.org.get_membership(self.agent).team)

        # try updating ourselves...
        update_url = reverse("orgs.user_update", args=[self.admin.id])

        # can't be updated because no other admins
        response = self.assertUpdateSubmit(update_url, self.admin, {"role": "E"}, object_unchanged=self.admin)
        self.assertRedirect(response, reverse("orgs.user_list"))
        self.assertEqual({self.user, self.editor}, set(self.org.get_users(roles=[OrgRole.EDITOR])))
        self.assertEqual({self.admin}, set(self.org.get_users(roles=[OrgRole.ADMINISTRATOR])))

        # add another admin to workspace and try again
        self.org.add_user(self.admin2, OrgRole.ADMINISTRATOR)

        response = self.assertUpdateSubmit(update_url, self.admin, {"role": "E"}, object_unchanged=self.admin)
        self.assertRedirect(response, reverse("orgs.org_start"))  # no longer have access to user list page

        self.assertEqual({self.user, self.editor, self.admin}, set(self.org.get_users(roles=[OrgRole.EDITOR])))
        self.assertEqual({self.admin2}, set(self.org.get_users(roles=[OrgRole.ADMINISTRATOR])))

    def test_delete(self):
        delete_url = reverse("orgs.user_delete", args=[self.agent.id])

        # nobody can access if users feature not enabled
        response = self.requestView(delete_url, self.admin)
        self.assertRedirect(response, reverse("orgs.org_workspace"))

        self.org.features = [Org.FEATURE_USERS]
        self.org.save(update_fields=("features",))

        self.assertRequestDisallowed(delete_url, [None, self.user, self.editor, self.agent])

        # check can't delete user not in the current org
        self.assertRequestDisallowed(reverse("orgs.user_delete", args=[self.admin2.id]), [self.admin])

        response = self.assertDeleteFetch(delete_url, [self.admin], as_modal=True)
        self.assertContains(
            response, "You are about to remove the user <b>Agnes</b> from your workspace. Are you sure?"
        )

        # submitting the delete doesn't actually delete the user - only removes them from the org
        response = self.assertDeleteSubmit(delete_url, self.admin, object_unchanged=self.agent)

        self.assertRedirect(response, reverse("orgs.user_list"))
        self.assertEqual({self.user, self.editor, self.admin}, set(self.org.get_users()))

        # try deleting ourselves..
        delete_url = reverse("orgs.user_delete", args=[self.admin.id])

        # can't be removed because no other admins
        response = self.assertDeleteSubmit(delete_url, self.admin, object_unchanged=self.admin)
        self.assertRedirect(response, reverse("orgs.user_list"))
        self.assertEqual({self.user, self.editor, self.admin}, set(self.org.get_users()))

        # add another admin to workspace and try again
        self.org.add_user(self.admin2, OrgRole.ADMINISTRATOR)

        response = self.assertDeleteSubmit(delete_url, self.admin, object_unchanged=self.admin)

        # this time we could remove ourselves
        response = self.assertDeleteSubmit(delete_url, self.admin, object_unchanged=self.admin)
        self.assertRedirect(response, reverse("orgs.org_choose"))
        self.assertEqual({self.user, self.editor, self.admin2}, set(self.org.get_users()))

    def test_account(self):
        self.login(self.agent)

        response = self.client.get(reverse("orgs.user_account"))
        self.assertEqual(1, len(response.context["formax"].sections))

        self.login(self.admin)

        response = self.client.get(reverse("orgs.user_account"))
        self.assertEqual(1, len(response.context["formax"].sections))

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
        invitation = Invitation.create(self.org, self.admin, "invited@nyaruka.com", OrgRole.ADMINISTRATOR)

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


class InvitationCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_list(self):
        list_url = reverse("orgs.invitation_list")

        # nobody can access if users feature not enabled
        response = self.requestView(list_url, self.admin)
        self.assertRedirect(response, reverse("orgs.org_workspace"))

        self.org.features = [Org.FEATURE_USERS]
        self.org.save(update_fields=("features",))

        self.assertRequestDisallowed(list_url, [None, self.user, self.editor, self.agent])

        inv1 = Invitation.create(self.org, self.admin, "bob@nyaruka.com", OrgRole.EDITOR)
        inv2 = Invitation.create(
            self.org, self.admin, "jim@nyaruka.com", OrgRole.AGENT, team=self.org.default_ticket_team
        )

        response = self.assertListFetch(list_url, [self.admin], context_objects=[inv2, inv1])
        self.assertNotContains(response, "(All Topics)")

        self.org.features += [Org.FEATURE_TEAMS]
        self.org.save(update_fields=("features",))

        response = self.assertListFetch(list_url, [self.admin], context_objects=[inv2, inv1])
        self.assertContains(response, "(All Topics)")

    def test_create(self):
        create_url = reverse("orgs.invitation_create")

        # nobody can access if users feature not enabled
        response = self.requestView(create_url, self.admin)
        self.assertRedirect(response, reverse("orgs.org_workspace"))

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
            new_obj_query=Invitation.objects.filter(org=self.org, email="newguy@nyaruka.com", role_code="A").exclude(
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

        # invite an agent (defaults to default team)
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"email": "newagent@nyaruka.com", "role": "T"},
            new_obj_query=Invitation.objects.filter(
                org=self.org, email="newagent@nyaruka.com", role_code="T", team=self.org.default_ticket_team
            ),
        )

        # if we have a teams feature, we can select a team
        self.org.features += [Org.FEATURE_TEAMS]
        self.org.save(update_fields=("features",))
        sales = Team.create(self.org, self.admin, "New Team", topics=[])

        self.assertCreateFetch(create_url, [self.admin], form_fields={"email": None, "role": "E", "team": None})
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"email": "otheragent@nyaruka.com", "role": "T", "team": sales.id},
            new_obj_query=Invitation.objects.filter(
                org=self.org, email="otheragent@nyaruka.com", role_code="T", team=sales
            ),
        )

    def test_delete(self):
        inv1 = Invitation.create(self.org, self.admin, "bob@nyaruka.com", OrgRole.EDITOR)
        inv2 = Invitation.create(self.org, self.admin, "jim@nyaruka.com", OrgRole.AGENT)

        delete_url = reverse("orgs.invitation_delete", args=[inv1.id])

        # nobody can access if users feature not enabled
        response = self.requestView(delete_url, self.admin)
        self.assertRedirect(response, reverse("orgs.org_workspace"))

        self.org.features = [Org.FEATURE_USERS]
        self.org.save(update_fields=("features",))

        self.assertRequestDisallowed(delete_url, [None, self.user, self.editor, self.agent])

        response = self.assertDeleteFetch(delete_url, [self.admin], as_modal=True)
        self.assertContains(
            response, "You are about to cancel the invitation sent to <b>bob@nyaruka.com</b>. Are you sure?"
        )

        response = self.assertDeleteSubmit(delete_url, self.admin, object_deactivated=inv1)

        self.assertRedirect(response, reverse("orgs.invitation_list"))
        self.assertEqual({inv2}, set(self.org.invitations.filter(is_active=True)))


class ExportCRUDLTest(TembaTest):
    def test_download(self):
        export = TicketExport.create(
            self.org, self.admin, start_date=date.today() - timedelta(days=7), end_date=date.today(), with_fields=()
        )
        export.perform()

        self.assertEqual(1, self.admin.notifications.filter(notification_type="export:finished", is_seen=False).count())

        download_url = reverse("orgs.export_download", kwargs={"uuid": export.uuid})
        self.assertEqual(f"/export/download/{export.uuid}/", download_url)

        raw_url = export.get_raw_url()
        self.assertIn(f"{settings.STORAGE_URL}/orgs/{self.org.id}/ticket_exports/{export.uuid}.xlsx", raw_url)
        self.assertIn(f"tickets_{datetime.today().strftime(r'%Y%m%d')}.xlsx", raw_url)

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
        self.assertRedirect(response, f"/test-default/orgs/{self.org.id}/ticket_exports/{export.uuid}.xlsx")
