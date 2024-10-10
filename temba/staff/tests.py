from django.contrib.auth.models import Group
from django.urls import reverse

from temba.orgs.models import Org, OrgMembership, OrgRole
from temba.tests import CRUDLTestMixin, TembaTest, mock_mailroom
from temba.utils.views.mixins import TEMBA_MENU_SELECTION


class OrgCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_read(self):
        read_url = reverse("staff.org_read", args=[self.org.id])

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

    def test_list_and_update(self):
        self.setUpLocations()

        manage_url = reverse("staff.org_list")
        update_url = reverse("staff.org_update", args=[self.org.id])

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


class UserCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_list(self):
        list_url = reverse("staff.user_list")

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
        read_url = reverse("staff.user_read", args=[self.editor.id])

        # this is a customer support only view
        self.assertStaffOnly(read_url)

        response = self.requestView(read_url, self.customer_support)
        self.assertEqual(200, response.status_code)

    def test_update(self):
        update_url = reverse("staff.user_update", args=[self.editor.id])

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

    @mock_mailroom
    def test_delete(self, mr_mocks):
        delete_url = reverse("staff.user_delete", args=[self.editor.id])

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
        self.assertEqual(reverse("staff.user_list"), response["Temba-Success"])

        self.editor.refresh_from_db()
        self.assertFalse(self.editor.is_active)

        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)
