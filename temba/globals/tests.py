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

        flow1 = self.get_flow("color")
        flow2 = self.get_flow("favorites")

        flow1.global_dependencies.add(global1, global2)
        flow2.global_dependencies.add(global1)

        self.assertEqual(2, global1.get_usage_count())
        self.assertEqual(1, global2.get_usage_count())

        with self.assertNumQueries(1):
            g1, g2 = Global.get_with_usage(self.org)
            self.assertEqual(2, g1.get_usage_count())
            self.assertEqual(1, g2.get_usage_count())

        global1.release()
        global2.release()

        self.assertEqual(0, Global.objects.count())


class GlobalCRUDLTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.global1 = Global.get_or_create(self.org, self.admin, "org_name", "Org Name", "Acme Ltd")
        self.global2 = Global.get_or_create(self.org, self.admin, "access_token", "Access Token", "23464373")

    def test_list(self):
        list_url = reverse("globals.global_list")
        self.login(self.user)

        response = self.client.get(list_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(list_url)
        self.assertEqual(list(response.context["object_list"]), [self.global2, self.global1])
        self.assertContains(response, "Acme Ltd")
        self.assertContains(response, "23464373")

        response = self.client.get(list_url + "?search=access")
        self.assertEqual(list(response.context["object_list"]), [self.global2])
