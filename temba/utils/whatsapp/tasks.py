import logging
import time

import requests
from celery import shared_task
from django_redis import get_redis_connection

from django.utils import timezone

from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactURN
from temba.notifications.incidents.builtin import ChannelTemplatesFailedIncidentType
from temba.request_logs.models import HTTPLog
from temba.utils import chunk_list
from temba.utils.crons import cron_task

from . import update_api_version
from .templates import update_local_templates

logger = logging.getLogger(__name__)


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

        except requests.RequestException:
            num_errored += 1

            # if last 5 sync attempts have been errors, create an incident
            recent_is_errors = list(
                channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED)
                .order_by("-id")
                .values_list("is_error", flat=True)[:5]
            )
            if len(recent_is_errors) >= 5 and all(recent_is_errors):
                ChannelTemplatesFailedIncidentType.get_or_create(channel)

        except Exception as e:
            logger.error(f"Error refreshing whatsapp templates: {str(e)}", exc_info=True)

    return {"refreshed": num_refreshed, "errored": num_errored}
