import logging

import requests

from django.utils import timezone

from temba.request_logs.models import HTTPLog

logger = logging.getLogger(__name__)


def update_api_version(channel):
    start = timezone.now()
    try:
        response = channel.type.check_health(channel)
        api_status = response.json()
        version = api_status["meta"]["version"]
        if not version.startswith("v"):
            version = "v" + version
        channel.config.update(version=version)
        channel.save(update_fields=("config",))
    except requests.RequestException as e:
        HTTPLog.from_exception(HTTPLog.WHATSAPP_CHECK_HEALTH, e, start, channel=channel)
    except Exception as e:  # pragma: no cover
        logger.error(f"Error retrieving WhatsApp API version: {str(e)}", exc_info=True)
