from typing import Dict, Iterable
from urllib.parse import urlparse

import boto3
from botocore.client import Config

from django.core.files.storage import DefaultStorage

from temba.utils import json


class PublicFileStorage(DefaultStorage):
    default_acl = "public-read"


public_file_storage = PublicFileStorage()
public_file_storage.default_acl = "public-read"

_s3_client = None


def client():  # pragma: no cover
    """
    Returns our shared S3 client
    """
    from django.conf import settings

    global _s3_client
    if not _s3_client:
        session = boto3.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
        )
        _s3_client = session.client("s3", config=Config(retries={"max_attempts": 3}))

    return _s3_client


def split_url(url):
    """
    Given an S3 URL parses it and returns a tuple of the bucket and key suitable for S3 boto calls
    """
    parsed = urlparse(url)
    bucket = parsed.netloc.split(".")[0]
    path = parsed.path.lstrip("/")

    return bucket, path


def get_body(url):
    """
    Given an S3 URL, downloads the object and returns the read body
    """
    bucket, key = split_url(url)

    obj = client().get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


class EventStreamReader:
    """
    Util for reading payloads from an S3 event stream and reconstructing JSONL records as they become available
    """

    def __init__(self, event_stream):
        self.event_stream = event_stream
        self.buffer = bytearray()

    def __iter__(self) -> Iterable[Dict]:
        for event in self.event_stream:
            if "Records" in event:
                self.buffer.extend(event["Records"]["Payload"])

                lines = self.buffer.splitlines(keepends=True)

                # if last line doesn't end with \n then it's incomplete and goes back in the buffer
                if not lines[-1].endswith(b"\n"):
                    self.buffer = bytearray(lines[-1])
                    lines = lines[:-1]

                for line in lines:
                    yield json.loads(line.decode("utf-8"))
