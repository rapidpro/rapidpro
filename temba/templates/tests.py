from temba.tests import TembaTest

from .models import Template, TemplateTranslation


class TemplateTest(TembaTest):
    def test_templates(self):
        tt1 = TemplateTranslation.get_or_create(
            self.channel, "hello", "eng", "Hello {{1}}", 1, TemplateTranslation.STATUS_PENDING, "1234"
        )
        tt2 = TemplateTranslation.get_or_create(
            self.channel, "hello", "fra", "Bonjour {{1}}", 1, TemplateTranslation.STATUS_PENDING, "5678"
        )

        self.assertEqual(tt1.template, tt2.template)
        modified_on = tt1.template.modified_on

        tt3 = TemplateTranslation.get_or_create(
            self.channel, "hello", "fra", "Salut {{1}}", 1, TemplateTranslation.STATUS_PENDING, "5678"
        )

        self.assertTrue(tt3.template.modified_on > modified_on)
        self.assertEqual(1, Template.objects.filter(org=self.org).count())
        self.assertEqual(2, TemplateTranslation.objects.filter(channel=self.channel).count())

        # trim them
        TemplateTranslation.trim(self.channel, [tt1])

        # tt2 should be inactive now
        tt2.refresh_from_db()
        self.assertFalse(tt2.is_active)
