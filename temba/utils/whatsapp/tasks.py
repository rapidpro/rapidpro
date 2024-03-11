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
from temba.notifications.incidents.builtin import ChannelTemplatesFailedIncidentType
from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.utils import chunk_list
from temba.utils.crons import cron_task
from temba.utils.languages import alpha2_to_alpha3

from . import update_api_version

logger = logging.getLogger(__name__)

STATUS_MAPPING = {
    "PENDING": TemplateTranslation.STATUS_PENDING,
    "APPROVED": TemplateTranslation.STATUS_APPROVED,
    "REJECTED": TemplateTranslation.STATUS_REJECTED,
}


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

    transformed_components = defaultdict(dict)

    all_parts_supported = True

    for component in components:
        component_type = component["type"].lower()

        if component_type == "header":
            comp_params = []

            if component.get("format", "text").upper() == "TEXT":
                for match in VARIABLE_RE.findall(component.get("text", "")):
                    comp_params.append({"type": "text"})
            else:
                comp_params.append({"type": component["format"].lower()})
                all_parts_supported = False

            if comp_params:
                params[component_type] = comp_params
            transformed_components[component_type] = dict(content=component.get("text", ""), params=comp_params)
        elif component_type == "body":
            comp_params = []
            for match in VARIABLE_RE.findall(component.get("text", "")):
                comp_params.append({"type": "text"})
            if comp_params:
                params[component_type] = comp_params
            transformed_components[component_type] = dict(content=component.get("text", ""), params=comp_params)
        elif component_type == "buttons":
            buttons = component["buttons"]
            for idx, button in enumerate(buttons):
                comp_params = []
                if button["type"].lower() == "url":
                    for match in VARIABLE_RE.findall(button.get("url", "")):
                        comp_params.append({"type": "text"})
                        all_parts_supported = False
                if comp_params:
                    params[f"button.{idx}"] = comp_params
                transformed_components[f"button.{idx}"] = dict(content=button.get("text", ""), params=comp_params)
        else:
            transformed_components[component_type] = dict(content=component.get("text", ""), params=[])
    return params, transformed_components, all_parts_supported


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

        params, transformed_components, all_parts_supported = _extract_template_params(components)
        content_parts = []

        for component in components:
            if component["type"] in ["HEADER", "BODY", "FOOTER"]:
                if "text" not in component:
                    continue

                content_parts.append(component["text"])

        content = "\n\n".join(content_parts)
        variable_count = _calculate_variable_count(content)

        if not content_parts or not all_parts_supported:
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
            components=transformed_components,
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


@cron_task()
def refresh_whatsapp_templates():
    """
    Runs across all WhatsApp channels that have connected FB accounts and syncs the templates which are active.
    """

    num_refreshed, num_errored = 0, 0

    template_types = [t.code for t in Channel.get_types() if hasattr(t, "fetch_templates")]

    channels = Channel.objects.filter(
        is_active=True,
        channel_type__in=template_types,
        org__is_active=True,
        org__is_suspended=False,
    )

    for channel in channels:
        # for channels which have version in their config, refresh it
        if channel.config.get("version"):
            update_api_version(channel)

        try:
            templates = channel.type.fetch_templates(channel)

            update_local_templates(channel, templates)

            num_refreshed += 1

            # if we have an ongoing template incident, end it
            ongoing = channel.incidents.filter(
                incident_type=ChannelTemplatesFailedIncidentType.slug, ended_on=None
            ).first()
            if ongoing:
                ongoing.end()

        except Exception as e:
            num_errored += 1

            # if last 5 sync attempts have been errors, create an incident
            recent_is_errors = list(
                channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED)
                .order_by("-id")
                .values_list("is_error", flat=True)[:5]
            )
            if len(recent_is_errors) >= 5 and all(recent_is_errors):
                ChannelTemplatesFailedIncidentType.get_or_create(channel)

            logger.error(f"Error refreshing whatsapp templates: {str(e)}", exc_info=True)

    return {"refreshed": num_refreshed, "errored": num_errored}
