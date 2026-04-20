from temba.tests import TembaTest

from .contacts import format_urn, name_or_urn, urn_icon, urn_or_anon


class ContactsTest(TembaTest):
    def test_name_or_urn(self):
        contact1 = self.create_contact("", urns=[])
        contact2 = self.create_contact("Ann", urns=[])
        contact3 = self.create_contact("Bob", urns=["tel:+12024561111", "telegram:098761111"])
        contact4 = self.create_contact("", urns=["tel:+12024562222", "telegram:098762222"])

        self.assertEqual("", name_or_urn(contact1, self.org))
        self.assertEqual("Ann", name_or_urn(contact2, self.org))
        self.assertEqual("Bob", name_or_urn(contact3, self.org))
        self.assertEqual("(202) 456-2222", name_or_urn(contact4, self.org))

        with self.anonymous(self.org):
            self.assertEqual(f"{contact1.id:010}", name_or_urn(contact1, self.org))
            self.assertEqual("Ann", name_or_urn(contact2, self.org))
            self.assertEqual("Bob", name_or_urn(contact3, self.org))
            self.assertEqual(f"{contact4.id:010}", name_or_urn(contact4, self.org))

    def test_urn_or_anon(self):
        contact1 = self.create_contact("Bob", urns=[])
        contact2 = self.create_contact("Uri", urns=["tel:+12024561414", "telegram:098765432"])

        self.assertEqual("--", urn_or_anon(contact1, self.org))
        self.assertEqual("+1 202-456-1414", urn_or_anon(contact2, self.org))

        with self.anonymous(self.org):
            self.assertEqual(f"{contact1.id:010}", urn_or_anon(contact1, self.org))
            self.assertEqual(f"{contact2.id:010}", urn_or_anon(contact2, self.org))

    def test_urn_icon(self):
        contact = self.create_contact("Uri", urns=["tel:+1234567890", "telegram:098765432", "viber:346376373"])
        tel_urn, tg_urn, viber_urn = contact.urns.order_by("-priority")

        self.assertEqual("icon-phone", urn_icon(tel_urn))
        self.assertEqual("icon-telegram", urn_icon(tg_urn))
        self.assertEqual("", urn_icon(viber_urn))

    def test_format_urn(self):
        contact = self.create_contact("Uri", urns=["tel:+12024561414"])

        self.assertEqual("+1 202-456-1414", format_urn(contact.get_urn(), self.org))

        with self.anonymous(self.org):
            self.assertEqual("••••••••", format_urn(contact.get_urn(), self.org))
