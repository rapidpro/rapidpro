from django.conf import settings

from .client.exceptions import *  # noqa
from .client.types import *  # noqa
from .queue import *  # noqa


def get_client():
    from .client.client import MailroomClient

    return MailroomClient(settings.MAILROOM_URL, settings.MAILROOM_AUTH_TOKEN)
