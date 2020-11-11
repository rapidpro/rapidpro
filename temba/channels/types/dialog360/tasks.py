import logging
import re
import requests

from django_redis import get_redis_connection
from django.utils import timezone
from celery.task import task
from temba.channels.models import Channel
from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from requests import RequestException

from .type import LANGUAGE_MAPPING, STATUS_MAPPING

logger = logging.getLogger(__name__)

VARIABLE_RE = re.compile(r"{{(\d+)}}")


def _calculate_variable_count(content):
    """
    Utility method that extracts the number of variables in the passed in WhatsApp template
    """
    count = 0

    for match in VARIABLE_RE.findall(content):
        if int(match) > count:
            count = int(match)

    return count


@task(track_started=True, name="refresh_360_templates")
def refresh_360_templates():

    r = get_redis_connection()
    if r.get("refresh_360_templates"):
        return

    with r.lock("refresh_360_templates", timeout=1800):
        for channel in Channel.objects.filter(is_active=True, channel_type="D3"):
            # Check config, move on if missing
            if Channel.CONFIG_AUTH_TOKEN not in channel.config:
                continue

            start = timezone.now()
            try:
                url = "%s/v1/configs/templates" % channel.config.get(
                    Channel.CONFIG_BASE_URL, "https://waba.messagepipe.io"
                )

                headers = {
                    "D360-Api-Key": channel.config[Channel.CONFIG_AUTH_TOKEN],
                    "Content-Type": "application/json",
                }

                response = requests.get(url, headers)
                elapsed = (timezone.now() - start).total_seconds() * 1000

                HTTPLog.create_from_response(
                    HTTPLog.WHATSAPP_TEMPLATES_SYNCED, url, response, channel=channel, request_time=elapsed
                )

                if response.status_code != 200:
                    continue

                seen = []
                for template in response.json()["data"]["waba_templates"]:
                    if template["status"] not in STATUS_MAPPING:
                        continue

                    status = STATUS_MAPPING[template["status"]]

                    content_parts = []

                    all_supported = True
                    for component in template["components"]:
                        if component["type"] not in ["HEADER", "BODY", "FOOTER"]:
                            continue

                        if "text" not in component:
                            continue

                        if component["type"] in ["HEADER", "FOOTER"] and _calculate_variable_count(component["text"]):
                            all_supported = False

                        content_parts.append(component["text"])

                    if not content_parts or not all_supported:
                        continue

                    content = "\n\n".join(component_parts)
                    variable_count = _calculate_variable_count(content)

                    language, country = LANGUAGE_MAPPING.get(template["language"], (None, None))
                    if language is None:
                        status = TemplateTranslation.STATUS_UNSUPPORTED_LANGUAGE
                        language = template["language"]

                    translation = TemplateTranslation.get_or_create(
                        channel=channel,
                        name=template["name"],
                        language=language,
                        country=country,
                        content=content,
                        variable_count=variable_count,
                        status=status,
                    )

                    seen.append(translation)

                TemplateTranslation.trim(channel.seen)

            except RequestException as e:
                HTTPLog.create_from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, url, e, start, channel=channel)

            except Exception as e:
                logger.error(f"Error refresh dialog360 whatsapp templates: {str(e)}", exc_info=True)
