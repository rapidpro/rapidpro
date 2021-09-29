import gzip
import io
from datetime import datetime
from typing import Dict, List

import iso8601

from temba.utils import chunk_list, json
from temba.utils.s3.select import LOOKUPS

REVERSE_LOOKUPS = {v: k for k, v in LOOKUPS.items()}


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

    def select_object_content(self, Bucket, Key, Expression=None, **kwargs):
        stream = self.objects[(Bucket, Key)]
        stream.seek(0)
        zstream = gzip.GzipFile(fileobj=stream)

        records = []
        while True:
            line = zstream.readline()
            if not line:
                break

            record = json.loads(line.decode("utf-8"))

            if not Expression or select_matches(Expression, record):
                records.append(record)

        return {"Payload": MockEventStream(records)}


def select_matches(expression: str, record: dict) -> bool:
    """
    Our greatly simplified version of S3 select matching
    """
    conditions = _parse_expression(expression)
    for field, op, val in conditions:
        if not _condition_matches(field, op, val, record):
            return False
    return True


def _condition_matches(field, op, val, record: dict) -> bool:
    # find the value in the record
    actual = record
    for key in field.split("."):
        actual = actual[key]

    if isinstance(val, datetime):
        actual = iso8601.parse_date(actual)

    if op == "=":
        return actual == val
    elif op == ">=":
        return actual >= val
    elif op == ">":
        return actual > val
    elif op == "<=":
        return actual <= val
    elif op == "<":
        return actual < val
    elif op == "IN":
        return actual in val


def _parse_expression(exp: str) -> list:
    """
    Expressions we generate for S3 Select are very limited and don't require intelligent parsing
    """
    conditions = exp[33:].split(" AND ")
    parsed = []
    for con in conditions:
        col, op, val = con.split(" ", maxsplit=2)
        col = col[2:]  # remove alias prefix
        parsed.append((col, op, _parse_value(val)))

    return parsed


def _parse_value(val: str):
    if val.startswith("CAST('") and val.endswith("' AS TIMESTAMP)"):
        return iso8601.parse_date(val[6:31])
    elif val.startswith("("):
        return [_parse_value(v) for v in val[1:-1].split(", ")]
    elif val.startswith("'"):
        return val[1:-1]
    elif val[0].isdigit():
        return int(val)
    elif val == "TRUE":
        return True
    elif val == "FALSE":
        return False
