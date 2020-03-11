import logging

import zope.interface
from prometheus_client import Counter, Gauge

from django.conf import settings

from temba.utils.analytics.base import IMetricBackend

logger = logging.getLogger(__name__)


MESSAGE_COUNTS = Gauge("temba_message_counts", "Current count of messages", ["hostname", "direction", "state"])
RELAYER_SYNC = Gauge("temba_relayer_sync_seconds_duration", "Duration of relayer sync view", ["hostname"])
CONTACTS = Counter("temba_contacts_total", "Number of contacts", ["hostname", "action"])


@zope.interface.implementer(IMetricBackend)
class PrometheusBackend:
    """
    A metrics backend for Prometheus
    """

    def _get_hostname(self):
        # settings.HOSTNAME is actually service name (like textit.in), and settings.MACHINE_NAME is the name of the machine
        # (virtual/physical) that is part of the service
        return "%s.%s" % (settings.MACHINE_HOSTNAME, settings.HOSTNAME)

    def gauge(self, event, value=None):
        if value is None:
            value = 1

        if event.startswith("temba.current_outgoing"):
            MESSAGE_COUNTS.labels(hostname=self._get_hostname(), direction="outbound", state=event.split("_")[-1]).set(
                value
            )
        elif event.startswith("temba.current_incoming"):
            MESSAGE_COUNTS.labels(hostname=self._get_hostname(), direction="inbound", state=event.split("_")[1]).set(
                value
            )
        elif event == "temba.relayer_sync":
            RELAYER_SYNC.labels(hostname=self._get_hostname()).set(value)
        else:
            logger.warning(f"Unknown prometheus gauge metric {{event}} with value {{value}}")

    def increment(self, event, value=None):
        if value is None:
            value = 1

        if event == "temba.contact_created":
            CONTACTS.labels(hostname=self._get_hostname(), action="created").inc(value)
        else:
            logger.warning(f"Unknown prometheus counter metric {{event}} with value {{value}}")
