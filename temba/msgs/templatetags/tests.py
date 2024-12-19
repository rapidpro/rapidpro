from django.template import Context, Template

from temba.tests import TembaTest

from . import sms as tags


class TestTemplateTags(TembaTest):
    def test_attachment_button(self):
        # complete attachment with subtype and extension
        self.assertEqual(
            {
                "content_type": "image/jpeg",
                "category": "image",
                "preview": "JPEG",
                "url": "https://example.com/test.jpg",
                "is_playable": False,
                "thumb": None,
            },
            tags.attachment_button("image/jpeg:https://example.com/test.jpg"),
        )

        # now with thumbnail
        self.assertEqual(
            {
                "content_type": "image/jpeg",
                "category": "image",
                "preview": "JPEG",
                "url": "https://example.com/test.jpg",
                "is_playable": False,
                "thumb": "https://example.com/test.jpg",
            },
            tags.attachment_button("image/jpeg:https://example.com/test.jpg", True),
        )

        # missing extension and thus no subtype
        self.assertEqual(
            {
                "content_type": "image",
                "category": "image",
                "preview": "IMAGE",
                "url": "https://example.com/test.aspx",
                "is_playable": False,
                "thumb": None,
            },
            tags.attachment_button("image:https://example.com/test.aspx"),
        )

        # ogg file with wrong content type
        self.assertEqual(
            {
                "content_type": "audio/ogg",
                "category": "audio",
                "preview": "OGG",
                "url": "https://example.com/test.ogg",
                "is_playable": True,
                "thumb": None,
            },
            tags.attachment_button("application/octet-stream:https://example.com/test.ogg"),
        )

        # geo coordinates
        self.assertEqual(
            {
                "content_type": "geo",
                "category": "geo",
                "preview": "-35.998287,26.478109",
                "url": "http://www.openstreetmap.org/?mlat=-35.998287&mlon=26.478109#map=18/-35.998287/26.478109",
                "is_playable": False,
                "thumb": None,
            },
            tags.attachment_button("geo:-35.998287,26.478109"),
        )

        context = Context(tags.attachment_button("image/jpeg:https://example.com/test.jpg"))
        template = Template("""{% load sms %}{% attachment_button "image/jpeg:https://example.com/test.jpg" %}""")

        rendered = template.render(context)
        self.assertIn("attachment", rendered)
        self.assertIn("src='https://example.com/test.jpg'", rendered)

    def test_render(self):
        def render_template(src, context=None):
            context = context or {}
            context = Context(context)
            return Template(src).render(context)

        template_src = "{% load sms %}{% render as foo %}123<a>{{ bar }}{% endrender %}-{{ foo }}-"
        self.assertEqual(render_template(template_src, {"bar": "abc"}), "-123<a>abc-")

        # exception if tag not used correctly
        self.assertRaises(ValueError, render_template, "{% load sms %}{% render with bob %}{% endrender %}")
        self.assertRaises(ValueError, render_template, "{% load sms %}{% render as %}{% endrender %}")
