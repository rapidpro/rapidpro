from temba.tests import TembaTest
from temba.tests.s3 import MockEventStream

from .s3 import EventStreamReader


class EventStreamReaderTest(TembaTest):
    def test_buffer(self):
        stream = MockEventStream(records=[{"id": 1, "text": "Hi"}], max_payload_size=256,)

        buffer = EventStreamReader(stream)
        self.assertEqual([{"id": 1, "text": "Hi"}], list(buffer))

        stream = MockEventStream(
            records=[{"id": 1, "text": "Hi"}, {"id": 2, "text": "Hi"}, {"id": 3, "text": "Hi"}], max_payload_size=5,
        )

        buffer = EventStreamReader(stream)
        self.assertEqual([{"id": 1, "text": "Hi"}, {"id": 2, "text": "Hi"}, {"id": 3, "text": "Hi"}], list(buffer))
