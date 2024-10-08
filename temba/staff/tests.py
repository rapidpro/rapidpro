from django.contrib.auth.models import Group
from django.urls import reverse

from temba.orgs.models import OrgMembership, OrgRole
from temba.tests import CRUDLTestMixin, TembaTest, mock_mailroom
from temba.utils.views.mixins import TEMBA_MENU_SELECTION


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
