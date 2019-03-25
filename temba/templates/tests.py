from temba.tests import TembaTest

from .models import ChannelTemplate, Template


class TemplateTest(TembaTest):
    def test_templates(self):
        ct1 = ChannelTemplate.ensure_exists(
            self.channel, "hello", "eng", "Hello {{1}}", ChannelTemplate.STATUS_PENDING, "1234"
        )
        ct2 = ChannelTemplate.ensure_exists(
            self.channel, "hello", "fra", "Bonjour {{1}}", ChannelTemplate.STATUS_PENDING, "5678"
        )

        self.assertEqual(ct1.template, ct2.template)
        modified_on = ct1.template.modified_on

        ct3 = ChannelTemplate.ensure_exists(
            self.channel, "hello", "fra", "Salut {{1}}", ChannelTemplate.STATUS_PENDING, "5678"
        )

        self.assertTrue(ct3.template.modified_on > modified_on)
        self.assertEqual(1, Template.objects.filter(org=self.org).count())
        self.assertEqual(2, ChannelTemplate.objects.filter(channel=self.channel).count())
