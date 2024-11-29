from temba.contacts.models import ContactField
from temba.contacts.templatetags import contacts as tags
from temba.msgs.models import Msg
from temba.tests import TembaTest


class ContactsTest(TembaTest):
    def test_contact_field(self):
        gender = self.create_field("gender", "Gender", ContactField.TYPE_TEXT)
        age = self.create_field("age", "Age", ContactField.TYPE_NUMBER)
        joined = self.create_field("joined", "Joined", ContactField.TYPE_DATETIME)
        last_seen_on = self.org.fields.get(key="last_seen_on")
        contact = self.create_contact("Bob", fields={"age": 30, "gender": "M", "joined": "2024-01-01T00:00:00Z"})

        self.assertEqual("M", tags.contact_field(contact, gender))
        self.assertEqual("30", tags.contact_field(contact, age))
        self.assertEqual(
            "<temba-date value='2024-01-01T02:00:00+02:00' display='date'></temba-date>",
            tags.contact_field(contact, joined),
        )
        self.assertEqual("--", tags.contact_field(contact, last_seen_on))

    def test_name_or_urn(self):
        contact1 = self.create_contact("", urns=[])
        contact2 = self.create_contact("Ann", urns=[])
        contact3 = self.create_contact("Bob", urns=["tel:+12024561111", "telegram:098761111"])
        contact4 = self.create_contact("", urns=["tel:+12024562222", "telegram:098762222"])

        self.assertEqual("", tags.name_or_urn(contact1, self.org))
        self.assertEqual("Ann", tags.name_or_urn(contact2, self.org))
        self.assertEqual("Bob", tags.name_or_urn(contact3, self.org))
        self.assertEqual("(202) 456-2222", tags.name_or_urn(contact4, self.org))

        with self.anonymous(self.org):
            self.assertEqual(f"{contact1.id:010}", tags.name_or_urn(contact1, self.org))
            self.assertEqual("Ann", tags.name_or_urn(contact2, self.org))
            self.assertEqual("Bob", tags.name_or_urn(contact3, self.org))
            self.assertEqual(f"{contact4.id:010}", tags.name_or_urn(contact4, self.org))

    def test_urn_or_anon(self):
        contact1 = self.create_contact("Bob", urns=[])
        contact2 = self.create_contact("Uri", urns=["tel:+12024561414", "telegram:098765432"])

        self.assertEqual("--", tags.urn_or_anon(contact1, self.org))
        self.assertEqual("+1 202-456-1414", tags.urn_or_anon(contact2, self.org))

        with self.anonymous(self.org):
            self.assertEqual(f"{contact1.id:010}", tags.urn_or_anon(contact1, self.org))
            self.assertEqual(f"{contact2.id:010}", tags.urn_or_anon(contact2, self.org))

    def test_urn_icon(self):
        contact = self.create_contact("Uri", urns=["tel:+1234567890", "telegram:098765432", "viber:346376373"])
        tel_urn, tg_urn, viber_urn = contact.urns.order_by("-priority")

        self.assertEqual("icon-phone", tags.urn_icon(tel_urn))
        self.assertEqual("icon-telegram", tags.urn_icon(tg_urn))
        self.assertEqual("", tags.urn_icon(viber_urn))

    def test_format_urn(self):
        contact = self.create_contact("Uri", urns=["tel:+12024561414"])

        self.assertEqual("+1 202-456-1414", tags.format_urn(contact.get_urn(), self.org))

        with self.anonymous(self.org):
            self.assertEqual("••••••••", tags.format_urn(contact.get_urn(), self.org))

    def test_msg_status_badge(self):
        contact = self.create_contact("Uri", urns=["tel:+12024561414"])
        msg = self.create_outgoing_msg(contact, "This is an outgoing message")

        # wired has a primary color check
        msg.status = Msg.STATUS_WIRED
        self.assertIn('"check"', tags.msg_status_badge(msg))
        self.assertIn("--color-primary-dark", tags.msg_status_badge(msg))

        # delivered has a success check
        msg.status = Msg.STATUS_DELIVERED
        self.assertIn('"check"', tags.msg_status_badge(msg))
        self.assertIn("--success-rgb", tags.msg_status_badge(msg))

        # errored show retrying icon
        msg.status = Msg.STATUS_ERRORED
        self.assertIn('"retry"', tags.msg_status_badge(msg))

        # failed messages show an x
        msg.status = Msg.STATUS_FAILED
        self.assertIn('"x"', tags.msg_status_badge(msg))
