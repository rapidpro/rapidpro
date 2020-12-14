import time

import requests

from django.core.management.base import BaseCommand

from temba.channels.models import Channel


class Command(BaseCommand):  # pragma: no cover
    help = "Reactivates Facebook channel webhooks"

    def handle(self, *args, **options):
        count = 0
        for channel in Channel.objects.filter(is_active=True, channel_type="FB"):
            response = requests.post(
                "https://graph.facebook.com/v3.3/me/subscribed_apps",
                params={"access_token": channel.config[Channel.CONFIG_AUTH_TOKEN]},
            )
            print(f"reactivating {channel.name}: {response.status_code}")
            time.sleep(2)

        print(f"reactivated {count} channels")
