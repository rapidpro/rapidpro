from temba.msgs.models import Attachment
from temba.tests import TembaTest


class AttachmentTest(TembaTest):
    def test_attachments(self):
        # check equality
        self.assertEqual(
            Attachment("image/jpeg", "http://example.com/test.jpg"),
            Attachment("image/jpeg", "http://example.com/test.jpg"),
        )

        # check parsing
        self.assertEqual(
            Attachment("image", "http://example.com/test.jpg"),
            Attachment.parse("image:http://example.com/test.jpg"),
        )
        self.assertEqual(
            Attachment("image/jpeg", "http://example.com/test.jpg"),
            Attachment.parse("image/jpeg:http://example.com/test.jpg"),
        )
        with self.assertRaises(ValueError):
            Attachment.parse("http://example.com/test.jpg")
