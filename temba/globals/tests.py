from django.test.utils import override_settings
from django.urls import reverse

from temba.tests import TembaTest

from .models import Global


class GlobalTest(TembaTest):
    def test_model(self):
        global1 = Global.get_or_create(self.org, self.admin, "org_name", "Org Name", "Acme Ltd")
        global2 = Global.get_or_create(self.org, self.admin, "access_token", "Access Token", "23464373")

        self.assertEqual("org_name", global1.key)
        self.assertEqual("Org Name", global1.name)
        self.assertEqual("Acme Ltd", global1.value)
        self.assertEqual("global[key=org_name,name=Org Name]", str(global1))

        # update value if provided
        g1 = Global.get_or_create(self.org, self.admin, "org_name", "Org Name", "Acme Holdings")
        self.assertEqual(global1, g1)

        global1.refresh_from_db()
        self.assertEqual("Acme Holdings", global1.value)

        # generate name if not provided
        global3 = Global.get_or_create(self.org, self.admin, "secret_value", name="", value="")
        self.assertEqual("secret_value", global3.key)
        self.assertEqual("Secret Value", global3.name)
        self.assertEqual("", global3.value)

        flow1 = self.get_flow("color")
        flow2 = self.get_flow("favorites")

        flow1.global_dependencies.add(global1, global2)
        flow2.global_dependencies.add(global1)

        self.assertEqual(2, global1.get_usage_count())
        self.assertEqual(1, global2.get_usage_count())

        with self.assertNumQueries(1):
            g1, g2, g3 = Global.annotate_usage(self.org.globals.order_by("id"))
            self.assertEqual(2, g1.get_usage_count())
            self.assertEqual(1, g2.get_usage_count())
            self.assertEqual(0, g3.get_usage_count())

        global1.release()
        global2.release()
        global3.release()

        self.assertEqual(0, Global.objects.count())

    def test_make_key(self):
        self.assertEqual("org_name", Global.make_key("Org Name"))
        self.assertEqual("account_name", Global.make_key("Account   Name  "))
        self.assertEqual("caf", Global.make_key("café"))
        self.assertEqual(
            "323_ffsn_slfs_ksflskfs_fk_anfaddgas",
            Global.make_key("  ^%$# %$$ $##323 ffsn slfs ksflskfs!!!! fk$%%%$$$anfaDDGAS ))))))))) "),
        )

    def test_is_valid_key(self):
        self.assertTrue(Global.is_valid_key("token"))
        self.assertTrue(Global.is_valid_key("token_now_2"))
        self.assertTrue(Global.is_valid_key("email"))
        self.assertFalse(Global.is_valid_key("Token"))  # must be lowercase
        self.assertFalse(Global.is_valid_key("token!"))  # can't have punctuation
        self.assertFalse(Global.is_valid_key("âge"))  # a-z only
        self.assertFalse(Global.is_valid_key("2up"))  # can't start with a number
        self.assertFalse(Global.is_valid_key("a" * 37))  # too long

    def test_is_valid_name(self):
        self.assertTrue(Global.is_valid_name("Age"))
        self.assertTrue(Global.is_valid_name("Age Now 2"))
        self.assertFalse(Global.is_valid_name("Age_Now"))  # can't have punctuation
        self.assertFalse(Global.is_valid_name("âge"))  # a-z only


class GlobalCRUDLTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.global1 = Global.get_or_create(self.org, self.admin, "org_name", "Org Name", "Acme Ltd")
        self.global2 = Global.get_or_create(self.org, self.admin, "access_token", "Access Token", "23464373")

        self.flow = self.get_flow("color")
        self.flow.global_dependencies.add(self.global1)

    def test_list_views(self):
        list_url = reverse("globals.global_list")
        self.login(self.user)

        response = self.client.get(list_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(list_url)
        self.assertEqual(list(response.context["object_list"]), [self.global2, self.global1])
        self.assertContains(response, "Acme Ltd")
        self.assertContains(response, "23464373")
        self.assertContains(response, "1 Use")

        response = self.client.get(list_url + "?search=access")
        self.assertEqual(list(response.context["object_list"]), [self.global2])

        unused_url = reverse("globals.global_unused")

        response = self.client.get(unused_url)
        self.assertEqual(list(response.context["object_list"]), [self.global2])

    @override_settings(MAX_ACTIVE_GLOBALS_PER_ORG=4)
    def test_create(self):
        create_url = reverse("globals.global_create")
        self.login(self.user)

        response = self.client.get(create_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(create_url)
        self.assertEqual(200, response.status_code)

        # we got a form with expected form fields
        self.assertEqual(["name", "value", "loc"], list(response.context["form"].fields.keys()))

        # try to submit with invalid name and missing value
        response = self.client.post(create_url, {"name": "/?:"})
        self.assertEqual(200, response.status_code)
        self.assertFormError(response, "form", "name", "Can only contain letters, numbers and hypens.")
        self.assertFormError(response, "form", "value", "This field is required.")

        # try to submit with name that would become invalid key
        response = self.client.post(create_url, {"name": "-"})
        self.assertEqual(200, response.status_code)
        self.assertFormError(response, "form", "name", "Isn't a valid name")

        # submit with valid values
        self.client.post(create_url, {"name": "Secret", "value": "[xyz]"})
        self.assertTrue(Global.objects.filter(org=self.org, name="Secret", value="[xyz]").exists())

        # try to submit with same name
        response = self.client.post(create_url, {"name": "Secret", "value": "[abc]"})
        self.assertEqual(200, response.status_code)
        self.assertFormError(response, "form", "name", "Must be unique.")

        # works if name is unique
        self.client.post(create_url, {"name": "Secret2", "value": "[abc]"})
        self.assertTrue(Global.objects.filter(org=self.org, name="Secret2", value="[abc]").exists())

        # try to create another now that we've reached the limit
        response = self.client.post(create_url, {"name": "Secret3", "value": "[xyz]"})
        self.assertEqual(200, response.status_code)
        self.assertFormError(response, "form", "__all__", "Cannot create a new global as limit is 4.")

    def test_update(self):
        update_url = reverse("globals.global_update", args=[self.global1.id])
        self.login(self.user)

        response = self.client.get(update_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(update_url)
        self.assertEqual(200, response.status_code)

        # we got a form with expected form fields
        self.assertEqual(["value", "loc"], list(response.context["form"].fields.keys()))

        # try to submit with missing value
        response = self.client.post(update_url, {})
        self.assertEqual(200, response.status_code)
        self.assertFormError(response, "form", "value", "This field is required.")

        self.client.post(update_url, {"value": "Acme Holdings"})

        self.global1.refresh_from_db()
        self.assertEqual("Org Name", self.global1.name)
        self.assertEqual("Acme Holdings", self.global1.value)

    def test_detail(self):
        detail_url = reverse("globals.global_detail", args=[self.global1.id])
        self.login(self.user)

        response = self.client.get(detail_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(detail_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual([self.flow], list(response.context["dep_flows"]))

    def test_delete(self):
        self.login(self.admin)

        delete_url = reverse("globals.global_delete", args=[self.global2.id])

        response = self.client.get(delete_url)
        self.assertContains(response, "Are you sure you want to delete this global?")

        response = self.client.post(delete_url)
        self.assertEqual(302, response.status_code)
        self.assertEqual(0, Global.objects.filter(id=self.global2.id).count())

        # can't delete if global is being used
        delete_url = reverse("globals.global_delete", args=[self.global1.id])

        response = self.client.get(delete_url)
        self.assertContains(response, "cannot be deleted because it is in use.")

        with self.assertRaises(ValueError):
            self.client.post(delete_url)

        self.assertEqual(1, Global.objects.filter(id=self.global1.id).count())
