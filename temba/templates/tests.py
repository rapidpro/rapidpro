from temba.tests import TembaTest

from .models import ChannelTemplate, Template


class TemplateTest(TembaTest):
    def test_templates(self):
        ct1 = ChannelTemplate.get_or_create(
            self.channel, "hello", "eng", "Hello {{1}}", 1, ChannelTemplate.STATUS_PENDING, "1234"
        )
        ct2 = ChannelTemplate.get_or_create(
            self.channel, "hello", "fra", "Bonjour {{1}}", 1, ChannelTemplate.STATUS_PENDING, "5678"
        )

        self.assertEqual(ct1.template, ct2.template)
        modified_on = ct1.template.modified_on

        ct3 = ChannelTemplate.get_or_create(
            self.channel, "hello", "fra", "Salut {{1}}", 1, ChannelTemplate.STATUS_PENDING, "5678"
        )

        self.assertTrue(ct3.template.modified_on > modified_on)
        self.assertEqual(1, Template.objects.filter(org=self.org).count())
        self.assertEqual(2, ChannelTemplate.objects.filter(channel=self.channel).count())

        # trim them
        ChannelTemplate.trim(self.channel, [ct1])

        # ct3 should be inactive now
        ct2.refresh_from_db()
        self.assertFalse(ct2.is_active)
