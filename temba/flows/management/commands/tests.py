from django.core.management import call_command
from django.utils import timezone

from temba.contacts.models import Contact
from temba.flows.models import FlowStart
from temba.tests import TembaTest
from temba.tests.engine import MockSessionWriter


class MigrateFlowsTest(TembaTest):
    def test_command(self):
        call_command("migrate_flows")


class InspectFlowsTest(TembaTest):
    def test_command(self):
        # flow which wrongly has has_issues set
        flow1 = self.create_flow("No Problems")
        flow1.has_issues = True
        flow1.save(update_fields=("has_issues",))

        # create flow with a bad_regex issue but clear has_issues
        flow2 = self.create_flow(
            "Bad Regex",
            nodes=[
                {
                    "uuid": "f3d5ccd0-fee0-4955-bcb7-21613f049eae",
                    "router": {
                        "type": "switch",
                        "categories": [
                            {
                                "uuid": "fc4ee6b0-af6f-42e3-ae84-153c313e390a",
                                "name": "Bad Regex",
                                "exit_uuid": "72a3f1da-bde1-4549-a986-d35809807be8",
                            },
                            {
                                "uuid": "78ae8f05-f92e-43b2-a886-406eaea1b8e0",
                                "name": "Other",
                                "exit_uuid": "72a3f1da-bde1-4549-a986-d35809807be8",
                            },
                        ],
                        "default_category_uuid": "78ae8f05-f92e-43b2-a886-406eaea1b8e0",
                        "operand": "@input.text",
                        "cases": [
                            {
                                "uuid": "98503572-25bf-40ce-ad72-8836b6549a38",
                                "type": "has_pattern",
                                "arguments": ["[["],
                                "category_uuid": "fc4ee6b0-af6f-42e3-ae84-153c313e390a",
                            }
                        ],
                    },
                    "exits": [{"uuid": "72a3f1da-bde1-4549-a986-d35809807be8"}],
                }
            ],
        )
        flow2.has_issues = False
        flow2.save(update_fields=("has_issues",))

        # create an invalid flow
        flow3 = self.create_flow("Invalid", nodes=[])
        flow3.revisions.all().update(definition={"foo": "bar"})

        call_command("inspect_flows")

        flow1.refresh_from_db()
        self.assertFalse(flow1.has_issues)

        flow2.refresh_from_db()
        self.assertTrue(flow2.has_issues)

        flow3.refresh_from_db()
        self.assertFalse(flow3.has_issues)


class UndoFootgunTest(TembaTest):
    def test_group_changes(self):
        flow = self.create_flow("Test")
        nodes = flow.get_definition()["nodes"]

        contact1 = self.create_contact("Joe", phone="1234")
        contact2 = self.create_contact("Frank", phone="2345")
        contact3 = self.create_contact("Anne", phone="3456")

        group1 = self.create_group("Group 1", contacts=[contact3])
        group2 = self.create_group("Group 2", contacts=[])
        group3 = self.create_group("Group 3", contacts=[contact1, contact2])

        # simulate a flow start which adds contacts 1 and 2 to groups 1 and 2, and removes them from group 3
        start1 = FlowStart.create(flow, self.admin, contacts=[contact1, contact2])
        (
            MockSessionWriter(contact1, flow, start=start1)
            .visit(nodes[0])
            .add_contact_groups([group1, group2])
            .remove_contact_groups([group3])
            .complete()
            .save()
        )
        (
            MockSessionWriter(contact2, flow, start=start1)
            .visit(nodes[0])
            .add_contact_groups([group1, group2])
            .remove_contact_groups([group3])
            .complete()
            .save()
        )

        # and another which adds contact 3 to group 3
        start2 = FlowStart.create(flow, self.admin, contacts=[contact3])
        (MockSessionWriter(contact3, flow, start=start2).visit(nodes[0]).add_contact_groups([group3]).complete().save())

        t0 = timezone.now()

        self.assertEqual({contact1, contact2, contact3}, set(group1.contacts.all()))
        self.assertEqual({contact1, contact2}, set(group2.contacts.all()))
        self.assertEqual({contact3}, set(group3.contacts.all()))

        # can run with --dry-run to preview changes
        call_command("undo_footgun", start=start1.id, dry_run=True, quiet=True)

        self.assertEqual({contact1, contact2, contact3}, set(group1.contacts.all()))
        self.assertEqual({contact1, contact2}, set(group2.contacts.all()))
        self.assertEqual({contact3}, set(group3.contacts.all()))

        # no contacts will have had modified_on updated
        self.assertEqual(0, Contact.objects.filter(modified_on__gt=t0).count())

        # and then actually make database changes
        call_command("undo_footgun", start=start1.id, quiet=True)

        self.assertEqual({contact3}, set(group1.contacts.all()))
        self.assertEqual(set(), set(group2.contacts.all()))
        self.assertEqual({contact1, contact2, contact3}, set(group3.contacts.all()))

        # contacts 1 and 2 will have had modified_on updated
        self.assertEqual(2, Contact.objects.filter(modified_on__gt=t0).count())

    def test_status_changes(self):
        flow = self.create_flow("Test")
        nodes = flow.get_definition()["nodes"]

        contact1 = self.create_contact("Joe", phone="1234")
        contact2 = self.create_contact("Frank", phone="2345")
        contact3 = self.create_contact("Anne", phone="3456")

        # simulate a flow start which adds blocks contact1 and stops contact2
        start1 = FlowStart.create(flow, self.admin, contacts=[contact1, contact2])
        (
            MockSessionWriter(contact1, flow, start=start1)
            .visit(nodes[0])
            .set_contact_status("blocked")
            .complete()
            .save()
        )
        (
            MockSessionWriter(contact2, flow, start=start1)
            .visit(nodes[0])
            .set_contact_status("stopped")
            .complete()
            .save()
        )

        t0 = timezone.now()

        self.assertEqual({contact1}, set(Contact.objects.filter(status="B")))
        self.assertEqual({contact2}, set(Contact.objects.filter(status="S")))
        self.assertEqual({contact3}, set(Contact.objects.filter(status="A")))

        # can run with --dry-run to preview changes
        call_command("undo_footgun", start=start1.id, dry_run=True, quiet=True)

        self.assertEqual({contact1}, set(Contact.objects.filter(status="B")))
        self.assertEqual({contact2}, set(Contact.objects.filter(status="S")))
        self.assertEqual({contact3}, set(Contact.objects.filter(status="A")))

        # no contacts will have had modified_on updated
        self.assertEqual(0, Contact.objects.filter(modified_on__gt=t0).count())

        # and then actually make database changes
        call_command("undo_footgun", start=start1.id, quiet=True)

        self.assertEqual({contact1, contact2, contact3}, set(Contact.objects.filter(status="A")))
