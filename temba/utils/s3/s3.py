from typing import Iterable
from urllib.parse import urlparse

from django.conf import settings
from django.core.files.storage import storages

from temba.utils import json

public_file_storage = storages["public"]


def client():
    """
    Returns an S3 client
    """
    return storages["default"].connection.meta.client


def split_url(url: str) -> tuple:
    """
    Given an S3 URL parses it and returns a tuple of the bucket and key suitable for S3 boto calls
    """
    url_parts = urlparse(url)

    if settings.AWS_S3_ADDRESSING_STYLE == "path":
        path_parts = url_parts.path[1:].split("/")
        return path_parts[0], "/".join(path_parts[1:])
    else:
        return url_parts.netloc.split(".")[0], url_parts.path[1:]


class EventStreamReader:
    """
    Util for reading payloads from an S3 event stream and reconstructing JSONL records as they become available
    """

    def __init__(self, event_stream):
        self.event_stream = event_stream
        self.buffer = bytearray()

    def __iter__(self) -> Iterable[dict]:
        for event in self.event_stream:
            if "Records" in event:
                self.buffer.extend(event["Records"]["Payload"])

                lines = self.buffer.splitlines(keepends=True)

                # if last line doesn't end with \n then it's incomplete and goes back in the buffer
                if not lines[-1].endswith(b"\n"):  # pragma: no cover
                    self.buffer = bytearray(lines[-1])
                    lines = lines[:-1]

                for line in lines:
                    yield json.loads(line.decode("utf-8"))
