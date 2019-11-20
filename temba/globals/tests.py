from temba.tests import TembaTest

from .models import Global


class GlobalTest(TembaTest):
    def test_model(self):
        global1 = Global.get_or_create(self.org, self.admin, "org_name", "Org Name", "Acme Ltd")

        self.assertEqual("org_name", global1.key)
        self.assertEqual("Org Name", global1.name)
        self.assertEqual("Acme Ltd", global1.value)

        global1.release()

        self.assertEqual(0, Global.objects.count())
