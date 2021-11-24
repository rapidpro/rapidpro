import logging
import re
import time

import requests
from django_redis import get_redis_connection

from django.utils import timezone

from celery import shared_task

from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactURN
from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.utils import chunk_list

from . import update_api_version
from .constants import LANGUAGE_MAPPING, STATUS_MAPPING

logger = logging.getLogger(__name__)


@shared_task(track_started=True, name="refresh_whatsapp_contacts")
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
                org_id=channel.org_id, scheme=URN.WHATSAPP_SCHEME, contact__status=Contact.STATUS_ACTIVE
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


def update_local_templates(channel, templates_data):

    channel_namespace = channel.config.get("fb_namespace", "")
    # run through all our templates making sure they are present in our DB
    seen = []
    for template in templates_data:

        template_status = template["status"]

        template_status = template_status.upper()
        # if this is a status we don't know about
        if template_status not in STATUS_MAPPING:
            continue

        status = STATUS_MAPPING[template_status]

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

        content = "\n\n".join(content_parts)
        variable_count = _calculate_variable_count(content)

        language, country = LANGUAGE_MAPPING.get(template["language"], (None, None))

        # its a (non fatal) error if we see a language we don't know
        if language is None:
            status = TemplateTranslation.STATUS_UNSUPPORTED_LANGUAGE
            language = template["language"]

        missing_external_id = f"{template['language']}/{template['name']}"
        translation = TemplateTranslation.get_or_create(
            channel=channel,
            name=template["name"],
            language=language,
            country=country,
            content=content,
            variable_count=variable_count,
            status=status,
            external_id=template.get("id", missing_external_id),
            namespace=template.get("namespace", channel_namespace),
        )

        seen.append(translation)

    # trim any translations we didn't see
    TemplateTranslation.trim(channel, seen)


@shared_task(track_started=True, name="refresh_whatsapp_templates")
def refresh_whatsapp_templates():
    """
    Runs across all WhatsApp templates that have connected FB accounts and syncs the templates which are active.
    """

    r = get_redis_connection()
    if r.get("refresh_whatsapp_templates"):  # pragma: no cover
        return

    with r.lock("refresh_whatsapp_templates", 1800):
        # for every whatsapp channel
        for channel in Channel.objects.filter(is_active=True, channel_type__in=["WA", "D3"]):

            # update the version only when have it set in the config
            if channel.config.get("version"):
                # fetches API version and saves on channel.config
                update_api_version(channel)
            # fetch all our templates
            try:

                templates_data, valid = channel.get_type().get_api_templates(channel)
                if not valid:
                    continue

                update_local_templates(channel, templates_data)

            except Exception as e:
                logger.error(f"Error refreshing whatsapp templates: {str(e)}", exc_info=True)
