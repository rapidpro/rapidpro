from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.timesince import timesince, timeuntil

from temba.campaigns.models import EventFire
from temba.utils.text import truncate


class Command(BaseCommand):
    """
    Lists event fires by scheduled on to help find late fires. There is an issue in mailroom where sometimes fires
    are marked in redis as fired but didn't actually fired, and because they are marked as fired in redis, we don't
    retry firing them. You can fix this with...

    from django_redis import get_redis_connection
    r = get_redis_connection()
    r.sismember("campaign_event_2021_09_10", "123456789")  # returns true
    r.srem("campaign_event_2021_09_10", "123456789")  # allows mailroom to re-fire event
    """

    help = "Lists unfired campaign events"

    def handle(self, *args, **options):
        unfired = EventFire.objects.filter(fired=None).select_related("event").order_by("scheduled", "id")[:50]

        self.stdout.write(f"Fire       | Event                            | Contact    | Scheduled")
        self.stdout.write(f"-----------|----------------------------------|------------|--------------")

        now = timezone.now()

        for fire in unfired:
            event = truncate(f"{fire.event.id}: {fire.event.name}", 32)
            contact = fire.contact_id

            if fire.scheduled > now:
                scheduled = timeuntil(fire.scheduled, now=now)
            else:
                scheduled = f"{timesince(fire.scheduled, now=now)} ago"

            self.stdout.write(f"{fire.id:10} | {event:<32} | {contact:10} | {scheduled}")
