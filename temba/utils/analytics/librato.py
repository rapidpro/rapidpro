import zope.interface
from librato_bg import Client as LibratoClient

from django.conf import settings

from temba.utils.analytics.base import IMetricBackend


@zope.interface.implementer(IMetricBackend)
class LibratoBackend:
    """
    A metrics backend for the librato service
    """

    def __init__(self, user, token):
        self._librato = LibratoClient(user, token)

    def _get_hostname(self):
        # settings.HOSTNAME is actually service name (like textit.in), and settings.MACHINE_NAME is the name of the machine
        # (virtual/physical) that is part of the service
        return "%s.%s" % (settings.MACHINE_HOSTNAME, settings.HOSTNAME)

    def gauge(self, event, value=None):
        if value is None:
            value = 1

        self._librato.gauge(event, value, self._get_hostname())

    def increment(self, event, value=None):
        # In librato, gauges are used as counters
        self.gauge(event, value)
