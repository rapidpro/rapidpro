from temba.tests import MigrationTest, TembaTest

from .models import Template, TemplateTranslation


class TemplateTest(TembaTest):
    def test_templates(self):
        tt1 = TemplateTranslation.get_or_create(
            self.channel, "hello", "eng", "US", "Hello {{1}}", 1, TemplateTranslation.STATUS_PENDING, "1234", ""
        )
        tt2 = TemplateTranslation.get_or_create(
            self.channel, "hello", "fra", "FR", "Bonjour {{1}}", 1, TemplateTranslation.STATUS_PENDING, "5678", ""
        )

        self.assertEqual(tt1.template, tt2.template)
        modified_on = tt1.template.modified_on

        tt3 = TemplateTranslation.get_or_create(
            self.channel,
            "hello",
            "fra",
            "FR",
            "Salut {{1}}",
            1,
            TemplateTranslation.STATUS_PENDING,
            "5678",
            "foo_namespace",
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


class PopulateNamespacesTest(MigrationTest):
    app = "templates"
    migrate_from = "0009_templatetranslation_namespace"
    migrate_to = "0010_populate_namespaces"

    def setUpBeforeMigration(self, apps):
        from temba.channels.models import Channel

        self.channel1 = Channel.create(
            self.org,
            self.user,
            "RW",
            "WA",
            name="Test Channel",
            address="+250785551212",
            config={"fb_namespace": "baz_namespace"},
        )

        self.tt1 = TemplateTranslation.get_or_create(
            self.channel1,
            "hello",
            "eng",
            "US",
            "Hi {{1}}",
            1,
            TemplateTranslation.STATUS_APPROVED,
            "1234",
            "",
        )

        self.channel2 = Channel.create(
            self.org,
            self.user,
            "RW",
            "WA",
            name="Test Channel",
            address="+250785551313",
            config={},
        )
        self.tt2 = TemplateTranslation.get_or_create(
            self.channel2,
            "hello",
            "eng",
            "US",
            "Hi {{1}}",
            1,
            TemplateTranslation.STATUS_APPROVED,
            "1234",
            "",
        )

    def test_migration(self):
        self.tt1.refresh_from_db()
        self.assertEqual(self.tt1.namespace, "baz_namespace")

        self.tt2.refresh_from_db()
        self.assertEqual(self.tt2.namespace, "")
