from django.test.utils import override_settings
from django.urls import reverse

from temba.globals.models import Global
from temba.tests import CRUDLTestMixin, TembaTest


class GlobalCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.global1 = Global.get_or_create(self.org, self.admin, "org_name", "Org Name", "Acme Ltd")
        self.global2 = Global.get_or_create(self.org, self.admin, "access_token", "Access Token", "23464373")
        self.other_org_global = Global.get_or_create(self.org2, self.admin, "access_token", "Access Token", "653732")

        self.flow = self.create_flow("Color Flow")
        self.flow.global_dependencies.add(self.global1)

    def test_list_and_unused(self):
        list_url = reverse("globals.global_list")
        unused_url = reverse("globals.global_unused")

        self.assertRequestDisallowed(list_url, [None, self.agent])
        response = self.assertListFetch(
            list_url, [self.user, self.editor, self.admin], context_objects=[self.global2, self.global1]
        )
        self.assertContains(response, "Acme Ltd")
        self.assertContains(response, "23464373")
        self.assertContains(response, "1 use")

        response = self.client.get(list_url + "?search=access")
        self.assertEqual(list(response.context["object_list"]), [self.global2])

        self.assertListFetch(unused_url, [self.user, self.editor, self.admin], context_objects=[self.global2])
        self.assertContentMenu(list_url, self.admin, ["New"])

    @override_settings(ORG_LIMIT_DEFAULTS={"globals": 4})
    def test_create(self):
        create_url = reverse("globals.global_create")

        self.assertRequestDisallowed(create_url, [None, self.user, self.agent])
        self.assertCreateFetch(create_url, [self.editor, self.admin], form_fields=["name", "value"])

        # try to submit with invalid name and missing value
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "/?:"},
            form_errors={"name": "Can only contain letters, numbers and hypens.", "value": "This field is required."},
        )

        # try to submit with name that would become invalid key
        self.assertCreateSubmit(
            create_url, self.admin, {"name": "-", "value": "123"}, form_errors={"name": "Isn't a valid name"}
        )

        # submit with valid values
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Secret", "value": "[xyz]"},
            success_status=200,
            new_obj_query=Global.objects.filter(org=self.org, name="Secret", value="[xyz]"),
        )

        # try to submit with same name
        self.assertCreateSubmit(
            create_url, self.admin, {"name": "Secret", "value": "[abc]"}, form_errors={"name": "Must be unique."}
        )

        # works if name is unique
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Secret2", "value": "[abc]"},
            success_status=200,
            new_obj_query=Global.objects.filter(org=self.org, name="Secret2", value="[abc]"),
        )

        # try to create another now that we've reached the limit
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Secret3", "value": "[abc]"},
            form_errors={
                "__all__": "This workspace has reached its limit of 4 globals. You must delete existing ones before you can create new ones."
            },
        )

    def test_update(self):
        update_url = reverse("globals.global_update", args=[self.global1.id])

        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])
        self.assertUpdateFetch(update_url, [self.editor, self.admin], form_fields=["value"])

        # try to submit with missing value
        self.assertUpdateSubmit(
            update_url, self.admin, {}, form_errors={"value": "This field is required."}, object_unchanged=self.global1
        )

        self.assertUpdateSubmit(update_url, self.admin, {"value": "Acme Holdings"})

        self.global1.refresh_from_db()
        self.assertEqual("Org Name", self.global1.name)
        self.assertEqual("Acme Holdings", self.global1.value)

        # can't view update form for global in other org
        update_url = reverse("globals.global_update", args=[self.other_org_global.id])
        response = self.client.get(update_url)
        self.assertLoginRedirect(response)

        # can't update global in other org
        response = self.client.post(update_url, {"value": "436734573"})
        self.assertLoginRedirect(response)

        # global should be unchanged
        self.other_org_global.refresh_from_db()
        self.assertEqual("653732", self.other_org_global.value)

    def test_usages(self):
        detail_url = reverse("globals.global_usages", args=[self.global1.uuid])

        self.assertRequestDisallowed(detail_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(detail_url, [self.user, self.editor, self.admin], context_object=self.global1)

        self.assertEqual({"flow": [self.flow]}, {t: list(qs) for t, qs in response.context["dependents"].items()})

    def test_delete(self):
        delete_url = reverse("globals.global_delete", args=[self.global2.uuid])

        self.assertRequestDisallowed(delete_url, [None, self.user, self.agent, self.admin2])

        # fetch delete modal
        response = self.assertDeleteFetch(delete_url, [self.editor, self.admin])
        self.assertContains(response, "You are about to delete")

        response = self.assertDeleteSubmit(delete_url, self.admin, object_deactivated=self.global2, success_status=200)
        self.assertEqual("/global/", response["X-Temba-Success"])

        # should see warning if global is being used
        delete_url = reverse("globals.global_delete", args=[self.global1.uuid])

        self.assertFalse(self.flow.has_issues)

        response = self.assertDeleteFetch(delete_url, [self.admin])
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, "Color Flow")

        response = self.assertDeleteSubmit(delete_url, self.admin, object_deactivated=self.global1, success_status=200)
        self.assertEqual("/global/", response["X-Temba-Success"])

        self.flow.refresh_from_db()
        self.assertTrue(self.flow.has_issues)
        self.assertNotIn(self.global1, self.flow.global_dependencies.all())
