import logging
import re
import time
from collections import defaultdict

import requests
from celery import shared_task
from django_redis import get_redis_connection

from django.utils import timezone

from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactURN
from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.utils import chunk_list
from temba.utils.languages import alpha2_to_alpha3

from . import update_api_version

logger = logging.getLogger(__name__)

STATUS_MAPPING = dict(
    PENDING=TemplateTranslation.STATUS_PENDING,
    APPROVED=TemplateTranslation.STATUS_APPROVED,
    REJECTED=TemplateTranslation.STATUS_REJECTED,
)


@shared_task
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

            HTTPLog.from_response(HTTPLog.WHATSAPP_CONTACTS_REFRESHED, resp, start, timezone.now(), channel=channel)

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


def _extract_template_params(components):
    params = defaultdict(list)

    for component in components:
        component_type = component["type"].lower()

        if component_type == "header":
            if component.get("format", "text").upper() == "TEXT":
                for match in VARIABLE_RE.findall(component.get("text", "")):
                    params[component_type].append({"type": "text"})
            else:
                params[component_type].append({"type": component["format"].lower()})
        if component_type == "body":
            for match in VARIABLE_RE.findall(component.get("text", "")):
                params[component_type].append({"type": "text"})
        if component_type == "buttons":
            buttons = component["buttons"]
            for idx, button in enumerate(buttons):
                if button["type"].lower() == "url":
                    for match in VARIABLE_RE.findall(button.get("url", "")):
                        params[f"button.{idx}"].append({"type": "text"})
    return params


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

        components = template["components"]

        params = _extract_template_params(components)
        content_parts = []

        all_supported = True
        for component in components:
            if component["type"] not in ["HEADER", "BODY", "FOOTER"]:
                continue

            if "text" not in component:
                continue

            if component["type"] in ["HEADER", "FOOTER"] and _calculate_variable_count(component["text"]):
                all_supported = False

            content_parts.append(component["text"])

        content = "\n\n".join(content_parts)
        variable_count = _calculate_variable_count(content)

        if not content_parts or not all_supported:
            status = TemplateTranslation.STATUS_UNSUPPORTED_COMPONENTS

        missing_external_id = f"{template['language']}/{template['name']}"
        translation = TemplateTranslation.get_or_create(
            channel,
            template["name"],
            locale=parse_whatsapp_language(template["language"]),
            content=content,
            variable_count=variable_count,
            status=status,
            external_locale=template["language"],
            external_id=template.get("id", missing_external_id[:64]),
            namespace=template.get("namespace", channel_namespace),
            components=components,
            params=params,
        )

        seen.append(translation)

    # trim any translations we didn't see
    TemplateTranslation.trim(channel, seen)


def parse_whatsapp_language(lang) -> str:
    """
    Converts a WhatsApp language code which can be alpha2 ('en') or alpha2_country ('en_US') or alpha3 ('fil')
    to our locale format ('eng' or 'eng-US').
    """
    language, country = lang.split("_") if "_" in lang else [lang, None]
    if len(language) == 2:
        language = alpha2_to_alpha3(language)

    return f"{language}-{country}" if country else language


@shared_task
def refresh_whatsapp_templates():
    """
    Runs across all WhatsApp templates that have connected FB accounts and syncs the templates which are active.
    """

    r = get_redis_connection()
    if r.get("refresh_whatsapp_templates"):  # pragma: no cover
        return

    with r.lock("refresh_whatsapp_templates", 1800):
        # for every whatsapp channel
        for channel in Channel.objects.filter(is_active=True, channel_type__in=["WA", "D3", "D3C", "WAC"]):
            # update the version only when have it set in the config
            if channel.config.get("version"):
                # fetches API version and saves on channel.config
                update_api_version(channel)
            # fetch all our templates
            try:
                templates_data, valid = channel.type.get_api_templates(channel)
                if not valid:
                    continue

                update_local_templates(channel, templates_data)

            except Exception as e:
                logger.error(f"Error refreshing whatsapp templates: {str(e)}", exc_info=True)
