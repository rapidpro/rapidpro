import logging

import requests
from celery import shared_task
from django_redis import get_redis_connection

from django.utils import timezone

from temba.channels.models import Channel
from temba.request_logs.models import HTTPLog

logger = logging.getLogger(__name__)


@shared_task
def refresh_whatsapp_tokens():
    r = get_redis_connection()
    if r.get("refresh_whatsapp_tokens"):  # pragma: no cover
        return

    with r.lock("refresh_whatsapp_tokens", 1800):
        # iterate across each of our whatsapp channels and get a new token
        for channel in Channel.objects.filter(is_active=True, channel_type="WA").order_by("id"):
            try:
                url = channel.config["base_url"] + "/v1/users/login"

                start = timezone.now()
                resp = requests.post(
                    url, auth=(channel.config[Channel.CONFIG_USERNAME], channel.config[Channel.CONFIG_PASSWORD])
                )
                HTTPLog.from_response(HTTPLog.WHATSAPP_TOKENS_SYNCED, resp, start, timezone.now(), channel=channel)

                if resp.status_code != 200:
                    continue

                channel.config["auth_token"] = resp.json()["users"][0]["token"]
                channel.save(update_fields=["config"])
            except Exception as e:
                logger.error(f"Error refreshing whatsapp tokens: {str(e)}", exc_info=True)
