from temba.globals.models import Global
from temba.tests import TembaTest


class GlobalTest(TembaTest):
    def test_model(self):
        global1 = Global.get_or_create(self.org, self.admin, "org_name", "Org Name", "Acme Ltd")
        global2 = Global.get_or_create(self.org, self.admin, "access_token", "Access Token", "23464373")

        self.assertEqual("org_name", global1.key)
        self.assertEqual("Org Name", global1.name)
        self.assertEqual("Acme Ltd", global1.value)
        self.assertEqual("Org Name", str(global1))

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

        flow1 = self.create_flow("Flow 1")
        flow2 = self.create_flow("Flow 2")

        flow1.global_dependencies.add(global1, global2)
        flow2.global_dependencies.add(global1)

        with self.assertNumQueries(1):
            g1, g2, g3 = Global.annotate_usage(self.org.globals.order_by("id"))
            self.assertEqual(2, g1.usage_count)
            self.assertEqual(1, g2.usage_count)
            self.assertEqual(0, g3.usage_count)

        global1.release(self.admin)
        global2.release(self.admin)
        global3.release(self.admin)

        self.assertEqual(0, Global.objects.filter(is_active=True).count())

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
        self.assertFalse(Global.is_valid_name("Age>Now"))  # can't have punctuation
        self.assertTrue(Global.is_valid_name("API_KEY-2"))  # except underscores and hypens
        self.assertFalse(Global.is_valid_name("âge"))  # a-z only
