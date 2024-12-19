from django.test import override_settings
from django.urls import reverse

from temba.msgs.models import Label
from temba.tests import CRUDLTestMixin, TembaTest


class LabelCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_create(self):
        create_url = reverse("msgs.label_create")

        self.assertRequestDisallowed(create_url, [None, self.user, self.agent])
        self.assertCreateFetch(create_url, [self.editor, self.admin], form_fields=("name", "messages"))

        # try to create label with invalid name
        self.assertCreateSubmit(
            create_url, self.admin, {"name": '"Spam"'}, form_errors={"name": 'Cannot contain the character: "'}
        )

        # try again with valid name
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Spam"},
            new_obj_query=Label.objects.filter(name="Spam"),
        )

        # check that we can't create another with same name
        self.assertCreateSubmit(create_url, self.admin, {"name": "Spam"}, form_errors={"name": "Must be unique."})

        # create another label
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Spam 2"},
            new_obj_query=Label.objects.filter(name="Spam 2"),
        )

        # try creating a new label after reaching the limit on labels
        current_count = Label.get_active_for_org(self.org).count()
        with override_settings(ORG_LIMIT_DEFAULTS={"labels": current_count}):
            response = self.client.post(create_url, {"name": "CoolStuff"})
            self.assertFormError(
                response.context["form"],
                "name",
                "This workspace has reached its limit of 2 labels. "
                "You must delete existing ones before you can create new ones.",
            )

    def test_update(self):
        label1 = self.create_label("Spam")
        label2 = self.create_label("Sales")

        label1_url = reverse("msgs.label_update", args=[label1.id])
        label2_url = reverse("msgs.label_update", args=[label2.id])

        self.assertRequestDisallowed(label2_url, [None, self.user, self.agent, self.admin2])
        self.assertUpdateFetch(label2_url, [self.editor, self.admin], form_fields={"name": "Sales", "messages": None})

        # try to update to invalid name
        self.assertUpdateSubmit(
            label1_url,
            self.admin,
            {"name": '"Spam"'},
            form_errors={"name": 'Cannot contain the character: "'},
            object_unchanged=label1,
        )

        # update with valid name
        self.assertUpdateSubmit(label1_url, self.admin, {"name": "Junk"})

        label1.refresh_from_db()
        self.assertEqual("Junk", label1.name)

    def test_delete(self):
        label = self.create_label("Spam")

        delete_url = reverse("msgs.label_delete", args=[label.uuid])

        self.assertRequestDisallowed(delete_url, [None, self.user, self.agent, self.admin2])

        # fetch delete modal
        response = self.assertDeleteFetch(delete_url, [self.editor, self.admin], as_modal=True)
        self.assertContains(response, "You are about to delete")

        # submit to delete it
        response = self.assertDeleteSubmit(delete_url, self.admin, object_deactivated=label, success_status=200)
        self.assertEqual("/msg/", response["X-Temba-Success"])

        # reactivate
        label.is_active = True
        label.save()

        # add a dependency and try again
        flow = self.create_flow("Color Flow")
        flow.label_dependencies.add(label)
        self.assertFalse(flow.has_issues)

        response = self.assertDeleteFetch(delete_url, [self.admin])
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, "Color Flow")

        self.assertDeleteSubmit(delete_url, self.admin, object_deactivated=label, success_status=200)

        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)
        self.assertNotIn(label, flow.label_dependencies.all())
