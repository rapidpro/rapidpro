from temba.tests import TembaTest

from .contacts import urn_icon


class ContactsTest(TembaTest):
    def test_urn_icon(self):
        contact = self.create_contact("Uri", urns=["tel:+1234567890", "telegram:098765432", "viber:346376373"])
        tel_urn, tg_urn, viber_urn = contact.urns.order_by("-priority")

        self.assertEqual("icon-phone", urn_icon(tel_urn))
        self.assertEqual("icon-telegram", urn_icon(tg_urn))
        self.assertEqual("", urn_icon(viber_urn))
