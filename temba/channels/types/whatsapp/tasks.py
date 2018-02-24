# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import requests

from celery.task import task
from django_redis import get_redis_connection

from temba.channels.models import Channel
from temba.contacts.models import ContactURN, WHATSAPP_SCHEME
from temba.utils import chunk_list


@task(track_started=True, name='refresh_whatsapp_contacts')
def refresh_whatsapp_contacts(channel_id):
    r = get_redis_connection()
    key = 'refresh_whatsapp_contacts_%d' % channel_id

    # we can't use our non-overlapping task decorator as it creates a loop in the celery resolver when registering
    if r.get(key):  # pragma: no cover
        return

    channel = Channel.objects.filter(id=channel_id, is_active=True).first()
    if not channel:  # pragma: no cover
        return

    with r.lock(key, 3600):
        # look up all whatsapp URNs for this channel
        wa_urns = (
            ContactURN.objects
            .filter(org_id=channel.org_id, scheme=WHATSAPP_SCHEME, contact__is_stopped=False, contact__is_blocked=False)
            .exclude(contact=None)
            .only('id', 'path')
        )

        # 1,000 contacts at a time, we ask WhatsApp to look up our contacts based on the path
        refreshed = 0

        for urn_batch in chunk_list(wa_urns, 1000):
            # need to wait 10 seconds between each batch of 1000
            if refreshed > 0:  # pragma: no cover
                time.sleep(10)

            # build a list of the fully qualified numbers we have
            users = ["+%s" % u.path for u in urn_batch]
            payload = {
                "payload": {
                    "blocking": "wait",
                    "users": users
                }
            }

            # go fetch our contacts
            resp = requests.post(channel.config[Channel.CONFIG_BASE_URL] + '/api/check_contacts.php',
                                 json=payload,
                                 auth=(channel.config[Channel.CONFIG_USERNAME],
                                       channel.config[Channel.CONFIG_PASSWORD]))

            # if we had an error, break out
            if resp.status_code != 200 or resp.json().get('error', True):
                raise Exception("Received error refreshing contacts for %d", channel.id)

            refreshed += len(urn_batch)

        print("refreshed %d whatsapp urns for channel %d" % (refreshed, channel_id))
