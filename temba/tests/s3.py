import gzip
import io
from collections import defaultdict
from datetime import datetime
from typing import Dict, List
from unittest.mock import call

import iso8601
import regex

from temba.archives.models import FileAndHash, jsonlgz_iterate
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
        self.calls = defaultdict(list)

    def put_object(self, Bucket: str, Key: str, Body, **kwargs):
        self.calls["put_object"].append(call(Bucket=Bucket, Key=Key, Body=Body, **kwargs))

        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key, **kwargs):
        self.calls["get_object"].append(call(Bucket=Bucket, Key=Key, **kwargs))

        body = self.objects[(Bucket, Key)]
        body.seek(0)
        return {"Bucket": Bucket, "Key": Key, "Body": body}

    def delete_object(self, Bucket, Key, **kwargs):
        self.calls["delete_object"].append(call(Bucket=Bucket, Key=Key, **kwargs))

        del self.objects[(Bucket, Key)]

        return {"DeleteMarker": False, "VersionId": "versionId", "RequestCharged": "requester"}

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        matches = []
        for o in self.objects.keys():
            if o[1].startswith(Prefix):
                matches.append({"Key": o[1]})

        return dict(Contents=matches)

    def select_object_content(self, Bucket, Key, Expression=None, **kwargs):
        self.calls["select_object_content"].append(call(Bucket=Bucket, Key=Key, Expression=Expression, **kwargs))

        stream = self.objects[(Bucket, Key)]
        stream.seek(0)
        records = []

        for record in jsonlgz_iterate(stream):
            if select_matches(Expression, record):
                records.append(record)

        return {"Payload": MockEventStream(records)}


def jsonlgz_encode(records: list) -> tuple:
    stream = io.BytesIO()
    wrapper = FileAndHash(stream)
    gz = gzip.GzipFile(fileobj=wrapper, mode="wb")

    for record in records:
        gz.write(json.dumps(record).encode("utf-8"))
        gz.write(b"\n")
    gz.close()

    return stream, wrapper.hash.hexdigest(), wrapper.size


def select_matches(expression: str, record: dict) -> bool:
    """
    Our greatly simplified version of S3 select matching
    """
    conditions = _parse_expression(expression)
    for lh, op, rh in conditions:
        if not _condition_matches(lh, op, rh, record):
            return False
    return True


def _condition_matches(lh, op, rh, record: dict) -> bool:
    def _resolve(ref):
        ref = ref[2:]
        val = record
        for key in ref.split("."):
            return_all = False
            if key.endswith("[*]"):
                return_all = True
                key = key[:-3]

            if isinstance(val, list) and return_all:
                val = [o[key] for o in val]
            else:
                val = val[key]
        return val

    if isinstance(lh, str) and lh.startswith("s."):
        lh = _resolve(lh)
    if isinstance(rh, str) and rh.startswith("s."):
        rh = _resolve(rh)

    if isinstance(lh, datetime):
        rh = iso8601.parse_date(rh)
    elif isinstance(rh, datetime):
        lh = iso8601.parse_date(lh)

    if op == "=":
        return lh == rh
    elif op == ">=":
        return lh >= rh
    elif op == ">":
        return lh > rh
    elif op == "<=":
        return lh <= rh
    elif op == "<":
        return lh < rh
    elif op == "IN":
        return lh in rh


def _parse_expression(exp: str) -> list:
    """
    Expressions we generate for S3 Select are very limited and don't require intelligent parsing
    """
    conditions = exp[33:].split(" AND ")
    parsed = []
    for con in conditions:
        match = regex.match(r"(.*)\s(=|!=|>|>=|<|<=|IN)\s(.+)", con)
        lh, op, rh = match.group(1), match.group(2), match.group(3)

        parsed.append((_parse_value(lh), op, _parse_value(rh)))

    return parsed


def _parse_value(val: str):
    if val.startswith("s."):  # field reference
        return val
    elif val.startswith("CAST(") and val.endswith(" AS TIMESTAMP)"):
        val = regex.match(r"CAST\((.+) AS .+\)", val).group(1)
        if val.startswith("s."):
            return val
        else:
            return iso8601.parse_date(val[1:-1])
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
