import boto3
from botocore.client import Config

from django.conf import settings

_client = None


def get_client():  # pragma: no cover
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
        else:
            session = boto3.Session()

        _client = session.client(
            "dynamodb", endpoint_url=settings.DYNAMO_ENDPOINT_URL, config=Config(retries={"max_attempts": 3})
        )

    return _client
