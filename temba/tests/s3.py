import gzip
import io
from typing import Dict, List

from temba.utils import chunk_list, json


class MockEventStream:
    def __init__(self, records: List[Dict], max_payload_size: int = 256):
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


class MockS3Client:
    """
    A mock of the boto S3 client
    """

    def __init__(self):
        self.objects = {}

    def put_jsonl(self, bucket: str, key: str, records: List[Dict]):
        stream = io.BytesIO()
        gz = gzip.GzipFile(fileobj=stream, mode="wb")

        for record in records:
            gz.write(json.dumps(record).encode("utf-8"))
            gz.write(b"\n")
        gz.close()

        self.objects[(bucket, key)] = stream

    def get_object(self, Bucket, Key, **kwargs):
        stream = self.objects[(Bucket, Key)]
        stream.seek(0)
        return {"Bucket": Bucket, "Key": Key, "Body": stream}

    def delete_object(self, Bucket, Key, **kwargs):
        del self.objects[(Bucket, Key)]
        return {"DeleteMarker": False, "VersionId": "versionId", "RequestCharged": "requester"}

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        matches = []
        for o in self.objects.keys():
            if o[1].startswith(Prefix):
                matches.append({"Key": o[1]})

        return dict(Contents=matches)

    def select_object_content(self, Bucket, Key, **kwargs):
        stream = self.objects[(Bucket, Key)]
        stream.seek(0)
        zstream = gzip.GzipFile(fileobj=stream)

        records = []
        while True:
            line = zstream.readline()
            if not line:
                break

            # unlike real S3 we don't actually filter any records by expression
            records.append(json.loads(line.decode("utf-8")))

        return {"Payload": MockEventStream(records)}
