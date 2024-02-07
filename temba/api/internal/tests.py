from django.urls import reverse
from django.utils import timezone

from temba.api.tests import APITestMixin
from temba.contacts.models import ContactExport
from temba.notifications.types import ExportFinishedNotificationType
from temba.templates.models import TemplateTranslation
from temba.tests import TembaTest, matchers
from temba.tickets.models import TicketExport

NUM_BASE_REQUEST_QUERIES = 5  # number of db queries required for any API request


class EndpointsTest(APITestMixin, TembaTest):
    def test_notifications(self):
        endpoint_url = reverse("api.internal.notifications") + ".json"

        self.assertPostNotAllowed(endpoint_url)

        # simulate an export finishing
        export1 = ContactExport.create(self.org, self.admin)
        ExportFinishedNotificationType.create(export1)

        # simulate an export by another user finishing
        export2 = TicketExport.create(self.org, self.editor, start_date=timezone.now(), end_date=timezone.now())
        ExportFinishedNotificationType.create(export2)

        # and org being suspended
        self.org.suspend()

        self.assertGet(
            endpoint_url,
            [self.admin],
            results=[
                {
                    "type": "incident:started",
                    "created_on": matchers.ISODate(),
                    "target_url": "/incident/",
                    "is_seen": False,
                    "incident": {
                        "type": "org:suspended",
                        "started_on": matchers.ISODate(),
                        "ended_on": None,
                    },
                },
                {
                    "type": "export:finished",
                    "created_on": matchers.ISODate(),
                    "target_url": f"/export/download/{export1.uuid}/",
                    "is_seen": False,
                    "export": {"type": "contact", "num_records": None},
                },
            ],
        )

        # notifications are user specific
        self.assertGet(
            endpoint_url,
            [self.editor],
            results=[
                {
                    "type": "export:finished",
                    "created_on": matchers.ISODate(),
                    "target_url": f"/export/download/{export2.uuid}/",
                    "is_seen": False,
                    "export": {"type": "ticket", "num_records": None},
                },
            ],
        )

        # a DELETE marks all notifications as seen
        self.assertDelete(endpoint_url, self.admin)

        self.assertEqual(0, self.admin.notifications.filter(is_seen=False).count())
        self.assertEqual(2, self.admin.notifications.filter(is_seen=True).count())
        self.assertEqual(1, self.editor.notifications.filter(is_seen=False).count())

    def test_templates(self):
        endpoint_url = reverse("api.internal.templates") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        org2channel = self.create_channel("A", "Org2Channel", "123456", country="RW", org=self.org2)

        # create some templates
        tpl1 = TemplateTranslation.get_or_create(
            self.channel,
            "hello",
            locale="eng-US",
            content="Hi {{1}}",
            variable_count=1,
            status=TemplateTranslation.STATUS_APPROVED,
            external_id="1234",
            external_locale="en_US",
            namespace="foo_namespace",
            components={"body": {"content": "Hi {{1}}", "params": [{"type": "text"}]}},
            params={"body": [{"type": "text"}]},
        ).template
        TemplateTranslation.get_or_create(
            self.channel,
            "hello",
            locale="fra-FR",
            content="Bonjour {{1}}",
            variable_count=1,
            status=TemplateTranslation.STATUS_PENDING,
            external_id="5678",
            external_locale="fr_FR",
            namespace="foo_namespace",
            components={"body": {"content": "Bonjour {{1}}", "params": [{"type": "text"}]}},
            params={"body": [{"type": "text"}]},
        )
        tt = TemplateTranslation.get_or_create(
            self.channel,
            "hello",
            locale="afr-ZA",
            content="This is a template translation for a deleted channel {{1}}",
            variable_count=1,
            status=TemplateTranslation.STATUS_APPROVED,
            external_id="9012",
            external_locale="af_ZA",
            namespace="foo_namespace",
            components={
                "body": {
                    "content": "This is a template translation for a deleted channel {{1}}",
                    "params": [{"type": "text"}],
                }
            },
            params={"body": [{"type": "text"}]},
        )
        tt.is_active = False
        tt.save()

        tpl2 = TemplateTranslation.get_or_create(
            self.channel,
            "goodbye",
            locale="eng-US",
            content="Goodbye {{1}}",
            variable_count=1,
            status=TemplateTranslation.STATUS_PENDING,
            external_id="6789",
            external_locale="en_US",
            namespace="foo_namespace",
            components={"body": {"content": "Goodbye {{1}}", "params": [{"type": "text"}]}},
            params={"body": [{"type": "text"}]},
        ).template

        # templates on other org to test filtering
        TemplateTranslation.get_or_create(
            org2channel,
            "goodbye",
            locale="eng-US",
            content="Goodbye {{1}}",
            variable_count=1,
            status=TemplateTranslation.STATUS_APPROVED,
            external_id="1234",
            external_locale="en_US",
            namespace="bar_namespace",
            components={"body": {"content": "Goodbye {{1}}", "params": [{"type": "text"}]}},
            params={"body": [{"type": "text"}]},
        )
        TemplateTranslation.get_or_create(
            org2channel,
            "goodbye",
            locale="fra-FR",
            content="Salut {{1}}",
            variable_count=1,
            status=TemplateTranslation.STATUS_PENDING,
            external_id="5678",
            external_locale="fr_FR",
            namespace="bar_namespace",
            components=[
                {
                    "type": "BODY",
                    "text": "Salut {{1}}",
                    "example": {"body_text": [["Bob"]]},
                },
            ],
            params={"body": [{"type": "text"}]},
        )

        tpl1.refresh_from_db()
        tpl2.refresh_from_db()

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {
                    "name": "goodbye",
                    "uuid": str(tpl2.uuid),
                    "translations": [
                        {
                            "channel": {"name": self.channel.name, "uuid": self.channel.uuid},
                            "locale": "eng-US",
                            "namespace": "foo_namespace",
                            "status": "pending",
                            "components": {"body": {"content": "Goodbye {{1}}", "params": [{"type": "text"}]}},
                        },
                    ],
                    "created_on": matchers.ISODate(),
                    "modified_on": matchers.ISODate(),
                },
                {
                    "name": "hello",
                    "uuid": str(tpl1.uuid),
                    "translations": [
                        {
                            "channel": {"name": self.channel.name, "uuid": self.channel.uuid},
                            "locale": "eng-US",
                            "namespace": "foo_namespace",
                            "status": "approved",
                            "components": {"body": {"content": "Hi {{1}}", "params": [{"type": "text"}]}},
                        },
                        {
                            "channel": {"name": self.channel.name, "uuid": self.channel.uuid},
                            "locale": "fra-FR",
                            "namespace": "foo_namespace",
                            "status": "pending",
                            "components": {"body": {"content": "Bonjour {{1}}", "params": [{"type": "text"}]}},
                        },
                    ],
                    "created_on": matchers.ISODate(),
                    "modified_on": matchers.ISODate(),
                },
            ],
            num_queries=NUM_BASE_REQUEST_QUERIES + 3,
        )
