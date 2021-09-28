import io
from datetime import datetime
from unittest.mock import patch

import pytz

from temba.tests import TembaTest
from temba.tests.s3 import MockEventStream, MockS3Client
from temba.utils.s3 import EventStreamReader, compile_select, get_body, split_url


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


class SelectTest(TembaTest):
    def test_compile_select(self):
        self.assertEqual("", compile_select("s"))
        self.assertEqual("c.uuid = '12345'", compile_select("c", uuid="12345"))
        self.assertEqual("s.contact.uuid = '12345'", compile_select(contact__uuid="12345"))
        self.assertEqual(
            "s.uuid = '12345' AND s.id = 123 AND s.active = TRUE", compile_select(uuid="12345", id=123, active=True)
        )
        self.assertEqual(
            "s.created_on > CAST('2021-09-28T18:27:30.123456+00:00' AS TIMESTAMP)",
            compile_select(created_on__gt=datetime(2021, 9, 28, 18, 27, 30, 123456, pytz.UTC)),
        )
        self.assertEqual(
            "s.modified_on <= CAST('2021-09-28T18:27:30.123456+00:00' AS TIMESTAMP)",
            compile_select(modified_on__lte=datetime(2021, 9, 28, 18, 27, 30, 123456, pytz.UTC)),
        )
        self.assertEqual("s.flow.uuid IN ('1234', '2345')", compile_select(flow__uuid__in=("1234", "2345")))
