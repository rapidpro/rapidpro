import io
from unittest.mock import patch

from temba.tests import TembaTest
from temba.tests.s3 import MockEventStream, MockS3Client
from temba.utils.s3 import EventStreamReader, get_body, split_url


class S3Test(TembaTest):
    def test_buffer(self):
        # empty payload
        stream = MockEventStream(records=[], max_payload_size=256)

        buffer = EventStreamReader(stream)
        self.assertEqual([], list(buffer))

        # single record that fits in single payload
        stream = MockEventStream(records=[{"id": 1, "text": "Hi"}], max_payload_size=256)

        buffer = EventStreamReader(stream)
        self.assertEqual([{"id": 1, "text": "Hi"}], list(buffer))

        # multiple records that will be split across several small payloads
        stream = MockEventStream(
            records=[{"id": 1, "text": "Hi"}, {"id": 2, "text": "Hi"}, {"id": 3, "text": "Hi"}], max_payload_size=5
        )

        buffer = EventStreamReader(stream)
        self.assertEqual([{"id": 1, "text": "Hi"}, {"id": 2, "text": "Hi"}, {"id": 3, "text": "Hi"}], list(buffer))

    def test_split(self):
        bucket, url = split_url("https://foo.s3.aws.amazon.com/test/12345")
        self.assertEqual("foo", bucket)
        self.assertEqual("test/12345", url)

    def test_get_body(self):
        mock_s3 = MockS3Client()
        mock_s3.objects[("foo", "test/12345")] = io.StringIO("12345_content")

        with patch("temba.utils.s3.s3.client", return_value=mock_s3):
            body = get_body("https://foo.s3.aws.amazon.com/test/12345")
            self.assertEqual(body, "12345_content")
