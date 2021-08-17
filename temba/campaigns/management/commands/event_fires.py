from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.timesince import timesince, timeuntil

from temba.campaigns.models import EventFire
from temba.utils.text import truncate


class Command(BaseCommand):  # pragma: no cover
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
