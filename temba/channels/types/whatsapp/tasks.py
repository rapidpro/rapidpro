
import logging
import time

import requests
from django_redis import get_redis_connection

from celery.task import task

from temba.channels.models import Channel
from temba.contacts.models import WHATSAPP_SCHEME, ContactURN
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
            resp = requests.post(
                channel.config[Channel.CONFIG_BASE_URL] + "/v1/contacts", json=payload, headers=headers
            )

            # if we had an error, break out
            if resp.status_code != 200 or resp.json().get("error", True):
                raise Exception("Received error refreshing contacts for %d", channel.id)

            refreshed += len(urn_batch)

        print("refreshed %d whatsapp urns for channel %d" % (refreshed, channel_id))


@task(track_started=True, name="refresh_whatsapp_tokens")
def refresh_whatsapp_tokens():
    r = get_redis_connection()
    # TODO: we can't use our non-overlapping task decorator as it creates a loop in the celery resolver when registering
    if r.get("refresh_whatsapp_tokens"):  # pragma: no cover
        return

    with r.lock("refresh_whatsapp_tokens", 1800):
        # iterate across each of our whatsapp channels and get a new token
        for channel in Channel.objects.filter(is_active=True, channel_type="WA"):
            resp = requests.post(
                channel.config["base_url"] + "/v1/users/login",
                auth=(channel.config[Channel.CONFIG_USERNAME], channel.config[Channel.CONFIG_PASSWORD]),
            )

            if resp.status_code != 200:
                logger.error("Received non-200 response refreshing whatsapp token: %s", resp.content)
                continue

            channel.config["auth_token"] = resp.json()["users"][0]["token"]
            channel.save(update_fields=["config"])
