import json
import zlib

import boto3
from botocore.client import Config

from django.conf import settings

_client = None


def get_client():
    """
    Returns our shared DynamoDB client
    """

    global _client

    if not _client:
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            session = boto3.Session(
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_REGION,
            )
        else:  # pragma: no cover
            session = boto3.Session()

        _client = session.client(
            "dynamodb", endpoint_url=settings.DYNAMO_ENDPOINT_URL, config=Config(retries={"max_attempts": 3})
        )

    return _client


def table_name(logical_name: str) -> str:
    """
    Add optional prefix to name to allow multiple deploys in same region
    """
    return settings.DYNAMO_TABLE_PREFIX + logical_name


def load_jsongz(data: bytes) -> dict:
    """
    Loads a value from gzipped JSON
    """
    return json.loads(zlib.decompress(data, wbits=zlib.MAX_WBITS | 16))


def dump_jsongz(value: dict) -> bytes:
    """
    Dumps a value to gzipped JSON
    """
    return zlib.compress(json.dumps(value).encode("utf-8"), wbits=zlib.MAX_WBITS | 16)
