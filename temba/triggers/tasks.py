from django.utils import timezone

from celery.task import task

from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import ContactURN


@task(track_started=True, name="fire_follow_triggers")  # pragma: no cover
def fire_follow_triggers(channel_id, contact_urn_id):
    """
    Fires a follow trigger
    """
    urn = ContactURN.objects.select_related("contact").get(pk=contact_urn_id)
    channel = Channel.objects.get(id=channel_id)

    if channel.is_active and channel.org:
        event = ChannelEvent.create(channel, urn.identity, ChannelEvent.TYPE_FOLLOW, timezone.now())
        event.handle()
