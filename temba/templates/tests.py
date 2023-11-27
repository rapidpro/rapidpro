from temba.tests import MigrationTest, TembaTest

from .models import Template, TemplateTranslation


class TemplateTest(TembaTest):
    def test_templates(self):
        tt1 = TemplateTranslation.get_or_create(
            self.channel,
            "hello",
            language="eng",
            country="US",
            content="Hello {{1}}",
            variable_count=1,
            status=TemplateTranslation.STATUS_PENDING,
            external_id="1234",
            external_locale="en_US",
            namespace="",
            components=[
                {
                    "type": "BODY",
                    "text": "Hello {{1}}",
                    "example": {"body_text": [["Bob"]]},
                },
            ],
            params={"body": [{"type": "text"}]},
        )
        tt2 = TemplateTranslation.get_or_create(
            self.channel,
            "hello",
            language="fra",
            country="FR",
            content="Bonjour {{1}}",
            variable_count=1,
            status=TemplateTranslation.STATUS_PENDING,
            external_id="5678",
            external_locale="fr_FR",
            namespace="",
            components=[
                {
                    "type": "BODY",
                    "text": "Bonjour {{1}}",
                    "example": {"body_text": [["Bob"]]},
                },
            ],
            params={"body": [{"type": "text"}]},
        )

        self.assertEqual(tt1.template, tt2.template)
        modified_on = tt1.template.modified_on

        tt3 = TemplateTranslation.get_or_create(
            self.channel,
            "hello",
            language="fra",
            country="FR",
            content="Salut {{1}}",
            variable_count=1,
            status=TemplateTranslation.STATUS_PENDING,
            external_id="5678",
            external_locale="fr_FR",
            namespace="foo_namespace",
            components=[
                {
                    "type": "BODY",
                    "text": "Salut {{1}}",
                    "example": {"body_text": [["Bob"]]},
                },
            ],
            params={"body": [{"type": "text"}]},
        )

        self.assertTrue(tt3.template.modified_on > modified_on)
        self.assertEqual(tt3.namespace, "foo_namespace")
        self.assertEqual(1, Template.objects.filter(org=self.org).count())
        self.assertEqual(2, TemplateTranslation.objects.filter(channel=self.channel).count())

        # trim them
        TemplateTranslation.trim(self.channel, [tt1])

        # tt2 should be inactive now
        tt2.refresh_from_db()
        self.assertFalse(tt2.is_active)


class BackfillLocaleTest(MigrationTest):
    app = "templates"
    migrate_from = "0015_templatetranslation_external_locale_and_more"
    migrate_to = "0016_backfill_locale"

    def setUpBeforeMigration(self, apps):
        template = Template.objects.create(org=self.org, name="hello")

        self.tt1 = TemplateTranslation.objects.create(
            template=template,
            channel=self.channel,
            namespace="",
            content="",
            variable_count=0,
            components=[],
            params={},
            status="A",
            external_id="123",
            language="eng",
            country="US",
        )
        self.tt2 = TemplateTranslation.objects.create(
            template=template,
            channel=self.channel,
            namespace="",
            content="",
            variable_count=0,
            components=[],
            params={},
            status="A",
            external_id="124",
            language="deu",
            country=None,
        )

    def test_migration(self):
        self.tt1.refresh_from_db()
        self.tt2.refresh_from_db()

        self.assertEqual("eng-US", self.tt1.locale)
        self.assertEqual("en_US", self.tt1.external_locale)
        self.assertEqual("deu", self.tt2.locale)
        self.assertEqual("de", self.tt2.external_locale)
