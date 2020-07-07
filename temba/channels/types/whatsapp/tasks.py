import logging
import re
import time

import requests
from django_redis import get_redis_connection
from requests import RequestException

from django.utils import timezone

from celery.task import task

from temba.channels.models import Channel
from temba.contacts.models import WHATSAPP_SCHEME, ContactURN
from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.utils import chunk_list

logger = logging.getLogger(__name__)


@task(track_started=True, name="refresh_whatsapp_contacts")
def refresh_whatsapp_contacts(channel_id):
    r = get_redis_connection()
    key = "refresh_whatsapp_contacts_%d" % channel_id

    # we can't use our non-overlapping task decorator as it creates a loop in the celery resolver when registering
    if r.get(key):  # pragma: no cover
        return

    channel = Channel.objects.filter(id=channel_id, is_active=True).first()
    if not channel:  # pragma: no cover
        return

    with r.lock(key, 3600):
        # look up all whatsapp URNs for this channel
        wa_urns = (
            ContactURN.objects.filter(
                org_id=channel.org_id, scheme=WHATSAPP_SCHEME, contact__is_stopped=False, contact__is_blocked=False
            )
            .exclude(contact=None)
            .only("id", "path")
        )

        # 1,000 contacts at a time, we ask WhatsApp to look up our contacts based on the path
        refreshed = 0

        for urn_batch in chunk_list(wa_urns, 1000):
            # need to wait 10 seconds between each batch of 1000
            if refreshed > 0:  # pragma: no cover
                time.sleep(10)

            # build a list of the fully qualified numbers we have
            contacts = ["+%s" % u.path for u in urn_batch]
            payload = {"blocking": "wait", "contacts": contacts}

            # go fetch our contacts
            headers = {"Authorization": "Bearer %s" % channel.config[Channel.CONFIG_AUTH_TOKEN]}
            url = channel.config[Channel.CONFIG_BASE_URL] + "/v1/contacts"

            start = timezone.now()
            resp = requests.post(url, json=payload, headers=headers)
            elapsed = (timezone.now() - start).total_seconds() * 1000

            HTTPLog.create_from_response(
                HTTPLog.WHATSAPP_CONTACTS_REFRESHED, url, resp, channel=channel, request_time=elapsed
            )

            # if we had an error, break out
            if resp.status_code != 200:
                break

            refreshed += len(urn_batch)

        print("refreshed %d whatsapp urns for channel %d" % (refreshed, channel_id))


@task(track_started=True, name="refresh_whatsapp_tokens")
def refresh_whatsapp_tokens():
    r = get_redis_connection()
    if r.get("refresh_whatsapp_tokens"):  # pragma: no cover
        return

    with r.lock("refresh_whatsapp_tokens", 1800):
        # iterate across each of our whatsapp channels and get a new token
        for channel in Channel.objects.filter(is_active=True, channel_type="WA"):
            url = channel.config["base_url"] + "/v1/users/login"

            start = timezone.now()
            resp = requests.post(
                url, auth=(channel.config[Channel.CONFIG_USERNAME], channel.config[Channel.CONFIG_PASSWORD])
            )
            elapsed = (timezone.now() - start).total_seconds() * 1000

            HTTPLog.create_from_response(
                HTTPLog.WHATSAPP_TOKENS_SYNCED, url, resp, channel=channel, request_time=elapsed
            )

            if resp.status_code != 200:
                continue

            channel.config["auth_token"] = resp.json()["users"][0]["token"]
            channel.save(update_fields=["config"])


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


@task(track_started=True, name="refresh_whatsapp_templates")
def refresh_whatsapp_templates():
    """
    Runs across all WhatsApp templates that have connected FB accounts and syncs the templates which are active.
    """

    from .type import (
        CONFIG_FB_BUSINESS_ID,
        CONFIG_FB_ACCESS_TOKEN,
        CONFIG_FB_TEMPLATE_LIST_DOMAIN,
        LANGUAGE_MAPPING,
        STATUS_MAPPING,
        TEMPLATE_LIST_URL,
    )

    r = get_redis_connection()
    if r.get("refresh_whatsapp_templates"):  # pragma: no cover
        return

    with r.lock("refresh_whatsapp_templates", 1800):
        # for every whatsapp channel
        for channel in Channel.objects.filter(is_active=True, channel_type="WA"):
            # move on if we have no FB credentials
            if (
                CONFIG_FB_BUSINESS_ID not in channel.config or CONFIG_FB_ACCESS_TOKEN not in channel.config
            ):  # pragma: no cover
                continue

            # fetch all our templates
            start = timezone.now()
            try:
                # Retrieve the template domain, fallback to the default for channels
                # that have been setup earlier for backwards compatibility
                facebook_template_domain = channel.config.get(CONFIG_FB_TEMPLATE_LIST_DOMAIN, "graph.facebook.com")
                facebook_business_id = channel.config.get(CONFIG_FB_BUSINESS_ID)
                url = TEMPLATE_LIST_URL % (facebook_template_domain, facebook_business_id)

                # we should never need to paginate because facebook limits accounts to 255 templates
                response = requests.get(
                    url, params=dict(access_token=channel.config[CONFIG_FB_ACCESS_TOKEN], limit=255)
                )
                elapsed = (timezone.now() - start).total_seconds() * 1000

                HTTPLog.create_from_response(
                    HTTPLog.WHATSAPP_TEMPLATES_SYNCED, url, response, channel=channel, request_time=elapsed
                )

                if response.status_code != 200:  # pragma: no cover
                    continue

                # run through all our templates making sure they are present in our DB
                seen = []
                for template in response.json()["data"]:
                    # if this is a status we don't know about
                    if template["status"] not in STATUS_MAPPING:
                        logger.error(f"unknown whatsapp status: {template['status']}")
                        continue

                    status = STATUS_MAPPING[template["status"]]

                    content_parts = []

                    all_supported = True
                    for component in template["components"]:
                        if component["type"] not in ["HEADER", "BODY", "FOOTER"]:
                            logger.error(f"unknown component type: {component}")
                            continue

                        if component["type"] in ["HEADER", "FOOTER"] and _calculate_variable_count(component["text"]):
                            logger.error(f"unsupported component type wih variables: {component}")
                            all_supported = False

                        content_parts.append(component["text"])

                    if not content_parts or not all_supported:
                        continue

                    content = "\n\n".join(content_parts)
                    variable_count = _calculate_variable_count(content)

                    language, country = LANGUAGE_MAPPING.get(template["language"], (None, None))

                    # its a (non fatal) error if we see a language we don't know
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
                        external_id=template["id"],
                    )

                    seen.append(translation)

                # trim any translations we didn't see
                TemplateTranslation.trim(channel, seen)

            except RequestException as e:
                HTTPLog.create_from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, url, e, start, channel=channel)

            except Exception as e:
                logger.error(f"Error refreshing whatsapp templates: {str(e)}", exc_info=True)
