# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from celery.task import task
from temba.contacts.models import ContactURN
from temba.utils.mage import handle_new_contact
from temba.channels.models import Channel, ChannelEvent
from django.utils import timezone


@task(track_started=True, name='fire_follow_triggers')  # pragma: no cover
def fire_follow_triggers(channel_id, contact_urn_id, new_mage_contact=False):
    """
    Fires a follow trigger
    """
    urn = ContactURN.objects.select_related('contact').get(pk=contact_urn_id)
    contact = urn.contact  # for now, flows start against contacts rather than URNs
    channel = Channel.objects.get(id=channel_id)

    # if contact was just created in Mage then..
    # * its dynamic groups won't have been initialized
    # * we need to update our cached contact counts
    if new_mage_contact:
        handle_new_contact(contact.org, contact)

    if channel.is_active and channel.org:
        event = ChannelEvent.create(channel, urn.identity, ChannelEvent.TYPE_FOLLOW, timezone.now())
        event.handle()
