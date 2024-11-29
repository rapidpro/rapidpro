from unittest.mock import patch

from django.urls import reverse

from temba.contacts.models import Contact
from temba.msgs.models import Msg
from temba.tests import mock_mailroom
from temba.tests.engine import MockSessionWriter

from . import APITest


class ContactActionsEndpointTest(APITest):
    @mock_mailroom
    def test_endpoint(self, mr_mocks):
        endpoint_url = reverse("api.v2.contact_actions") + ".json"

        self.assertGetNotAllowed(endpoint_url)
        self.assertPostNotPermitted(endpoint_url, [None, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        for contact in Contact.objects.all():
            contact.release(self.admin)
            contact.delete()

        # create some contacts to act on
        contact1 = self.create_contact("Ann", phone="+250788000001")
        contact2 = self.create_contact("Bob", phone="+250788000002")
        contact3 = self.create_contact("Cat", phone="+250788000003")
        contact4 = self.create_contact("Don", phone="+250788000004")  # a blocked contact
        contact5 = self.create_contact("Eve", phone="+250788000005")  # a deleted contact
        contact4.block(self.user)
        contact5.release(self.user)

        group = self.create_group("Testers")
        self.create_field("isdeveloper", "Is developer")
        self.create_group("Developers", query="isdeveloper = YES")
        other_org_group = self.create_group("Testers", org=self.org2)

        # create some waiting runs for some of the contacts
        flow = self.create_flow("Favorites")
        MockSessionWriter(contact1, flow).wait().save()
        MockSessionWriter(contact2, flow).wait().save()
        MockSessionWriter(contact3, flow).wait().save()

        self.create_incoming_msg(contact1, "Hello")
        self.create_incoming_msg(contact2, "Hello")
        self.create_incoming_msg(contact3, "Hello")
        self.create_incoming_msg(contact4, "Hello")

        # try adding more contacts to group than this endpoint is allowed to operate on at one time
        self.assertPost(
            endpoint_url,
            self.agent,
            {"contacts": [str(x) for x in range(101)], "action": "add", "group": "Testers"},
            errors={"contacts": "Ensure this field has no more than 100 elements."},
        )

        # try adding all contacts to a group by its name
        self.assertPost(
            endpoint_url,
            self.editor,
            {
                "contacts": [contact1.uuid, "tel:+250788000002", contact3.uuid, contact4.uuid, contact5.uuid],
                "action": "add",
                "group": "Testers",
            },
            errors={"contacts": "No such object: %s" % contact5.uuid},
        )

        # try adding a blocked contact to a group
        self.assertPost(
            endpoint_url,
            self.admin,
            {
                "contacts": [contact1.uuid, contact2.uuid, contact3.uuid, contact4.uuid],
                "action": "add",
                "group": "Testers",
            },
            errors={"non_field_errors": "Non-active contacts cannot be added to groups: %s" % contact4.uuid},
        )

        # add valid contacts to the group by name
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid, "tel:+250788000002"], "action": "add", "group": "Testers"},
            status=204,
        )
        self.assertEqual(set(group.contacts.all()), {contact1, contact2})

        # try to add to a non-existent group
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid], "action": "add", "group": "Spammers"},
            errors={"group": "No such object: Spammers"},
        )

        # try to add to a dynamic group
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid], "action": "add", "group": "Developers"},
            errors={"group": "Contact group must not be query based: Developers"},
        )

        # add contact 3 to a group by its UUID
        self.assertPost(
            endpoint_url, self.admin, {"contacts": [contact3.uuid], "action": "add", "group": group.uuid}, status=204
        )
        self.assertEqual(set(group.contacts.all()), {contact1, contact2, contact3})

        # try adding with invalid group UUID
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact3.uuid], "action": "add", "group": "15611256-95b5-46d5-b857-abafe0d32fe9"},
            errors={"group": "No such object: 15611256-95b5-46d5-b857-abafe0d32fe9"},
        )

        # try to add to a group in another org
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact3.uuid], "action": "add", "group": other_org_group.uuid},
            errors={"group": f"No such object: {other_org_group.uuid}"},
        )

        # remove contact 2 from group by its name (which is case-insensitive)
        self.assertPost(
            endpoint_url, self.admin, {"contacts": [contact2.uuid], "action": "remove", "group": "testers"}, status=204
        )
        self.assertEqual(set(group.contacts.all()), {contact1, contact3})

        # and remove contact 3 from group by its UUID
        self.assertPost(
            endpoint_url, self.admin, {"contacts": [contact3.uuid], "action": "remove", "group": group.uuid}, status=204
        )
        self.assertEqual(set(group.contacts.all()), {contact1})

        # try to add to group without specifying a group
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid], "action": "add"},
            errors={"non_field_errors": 'For action "add" you should also specify a group'},
        )
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid], "action": "add", "group": ""},
            errors={"group": "This field may not be null."},
        )

        # block all contacts
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid, contact2.uuid, contact3.uuid, contact4.uuid], "action": "block"},
            status=204,
        )
        self.assertEqual(
            set(Contact.objects.filter(status=Contact.STATUS_BLOCKED)), {contact1, contact2, contact3, contact4}
        )

        # unblock contact 1
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid], "action": "unblock"},
            status=204,
        )
        self.assertEqual(set(self.org.contacts.filter(status=Contact.STATUS_ACTIVE)), {contact1, contact5})
        self.assertEqual(set(self.org.contacts.filter(status=Contact.STATUS_BLOCKED)), {contact2, contact3, contact4})

        # interrupt any active runs of contacts 1 and 2
        with patch("temba.mailroom.queue_interrupt") as mock_queue_interrupt:
            self.assertPost(
                endpoint_url,
                self.admin,
                {"contacts": [contact1.uuid, contact2.uuid], "action": "interrupt"},
                status=204,
            )

            mock_queue_interrupt.assert_called_once_with(self.org, contacts=[contact1, contact2])

        # archive all messages for contacts 1 and 2
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid, contact2.uuid], "action": "archive_messages"},
            status=204,
        )
        self.assertFalse(Msg.objects.filter(contact__in=[contact1, contact2], direction="I", visibility="V").exists())
        self.assertTrue(Msg.objects.filter(contact=contact3, direction="I", visibility="V").exists())

        # delete contacts 1 and 2
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact1.uuid, contact2.uuid], "action": "delete"},
            status=204,
        )
        self.assertEqual(set(self.org.contacts.filter(is_active=False)), {contact1, contact2, contact5})
        self.assertEqual(set(self.org.contacts.filter(is_active=True)), {contact3, contact4})
        self.assertFalse(Msg.objects.filter(contact__in=[contact1, contact2]).exclude(visibility="D").exists())
        self.assertTrue(Msg.objects.filter(contact=contact3).exclude(visibility="D").exists())

        # try to provide a group for a non-group action
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact3.uuid], "action": "block", "group": "Testers"},
            errors={"non_field_errors": 'For action "block" you should not specify a group'},
        )

        # trying to act on zero contacts is an error
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [], "action": "block"},
            errors={"contacts": "Contacts can't be empty."},
        )

        # try to invoke an invalid action
        self.assertPost(
            endpoint_url,
            self.admin,
            {"contacts": [contact3.uuid], "action": "like"},
            errors={"action": '"like" is not a valid choice.'},
        )
