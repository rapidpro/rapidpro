from librato_bg import Client

from django.conf import settings

from ..base import AnalyticsBackend


class LibratoBackend(AnalyticsBackend):
    slug = "librato"

    def __init__(self, user, token):
        self.client = Client(user, token)

    def gauge(self, event: str, value):
        source = f"{settings.MACHINE_HOSTNAME}.{settings.HOSTNAME}"  # e.g. rapid1.rapidpro.io
        self.client.gauge(event, value, source)

    def track(self, user, event: str, properties: dict):
        pass

    def identify(self, user, brand: dict, org):
        pass

    def change_consent(self, user, consent: bool):
        pass
