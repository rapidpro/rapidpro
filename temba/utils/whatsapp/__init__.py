import logging

import requests

from django.utils import timezone

from temba.channels.models import Channel
from temba.request_logs.models import HTTPLog

logger = logging.getLogger(__name__)


def update_api_version(channel):
    start = timezone.now()
    try:
        response = channel.get_type().check_health(channel)
        api_status = response.json()
        version = api_status["meta"]["version"]
        if not version.startswith("v"):
            version = "v" + version
        channel.config.update(version=version)
        channel.save()
    except requests.RequestException as e:
        HTTPLog.create_from_exception(
            HTTPLog.WHATSAPP_CHECK_HEALTH,
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/health",
            e,
            start,
            channel=channel,
        )
    except Exception as e:
        logger.error(f"Error retrieving WhatsApp API version: {str(e)}", exc_info=True)
