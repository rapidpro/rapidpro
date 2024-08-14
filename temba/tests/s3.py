import gzip
import io

from temba.archives.models import FileAndHash
from temba.utils import chunk_list, json


class MockEventStream:
    def __init__(self, records: list[dict], max_payload_size: int = 256):
        # serialize records as a JSONL payload
        buffer = io.BytesIO()
        for record in records:
            buffer.write(json.dumps(record).encode("utf-8"))
            buffer.write(b"\n")

        payload = buffer.getvalue()
        payload_chunks = chunk_list(payload, size=max_payload_size)

        self.events = [{"Records": {"Payload": chunk}} for chunk in payload_chunks]
        self.events.append(
            {"Stats": {"Details": {"BytesScanned": 123, "BytesProcessed": 234, "BytesReturned": len(payload)}}},
        )
        self.events.append({"End": {}})

    def __iter__(self):
        for event in self.events:
            yield event


def jsonlgz_encode(records: list) -> tuple:
    stream = io.BytesIO()
    wrapper = FileAndHash(stream)
    gz = gzip.GzipFile(fileobj=wrapper, mode="wb")

    for record in records:
        gz.write(json.dumps(record).encode("utf-8"))
        gz.write(b"\n")
    gz.close()

    return stream, wrapper.hash.hexdigest(), wrapper.size
