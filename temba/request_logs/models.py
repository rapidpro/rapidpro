import logging

import requests

from django.db import models
from django.db.models import Index, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.airtime.models import AirtimeTransfer
from temba.channels.models import Channel
from temba.classifiers.models import Classifier
from temba.flows.models import Flow
from temba.orgs.models import Org
from temba.utils import redact
from temba.utils.http import HttpLog

logger = logging.getLogger(__name__)


class HTTPLog(models.Model):
    """
    HTTPLog is used to log HTTP requests and responses.
    """

    REDACT_MASK = "*" * 8  # used to mask redacted values
    HEALTHY_TIME_LIMIT = 10_000  # a call that takes longer than 10 seconds is considered unhealthy

    # log type choices
    WEBHOOK_CALLED = "webhook_called"
    INTENTS_SYNCED = "intents_synced"
    CLASSIFIER_CALLED = "classifier_called"
    AIRTIME_TRANSFERRED = "airtime_transferred"
    WHATSAPP_TEMPLATES_SYNCED = "whatsapp_templates_synced"
    WHATSAPP_TOKENS_SYNCED = "whatsapp_tokens_synced"
    WHATSAPP_CONTACTS_REFRESHED = "whatsapp_contacts_refreshed"
    WHATSAPP_CHECK_HEALTH = "whataspp_check_health"

    # possible log type choices and descriptive names
    LOG_TYPE_CHOICES = (
        (WEBHOOK_CALLED, "Webhook Called"),
        (INTENTS_SYNCED, _("Intents Synced")),
        (CLASSIFIER_CALLED, _("Classifier Called")),
        (AIRTIME_TRANSFERRED, _("Airtime Transferred")),
        (WHATSAPP_TEMPLATES_SYNCED, _("WhatsApp Templates Synced")),
        (WHATSAPP_TOKENS_SYNCED, _("WhatsApp Tokens Synced")),
        (WHATSAPP_CONTACTS_REFRESHED, _("WhatsApp Contacts Refreshed")),
        (WHATSAPP_CHECK_HEALTH, _("WhatsApp Health Check")),
    )

    org = models.ForeignKey(Org, related_name="http_logs", on_delete=models.PROTECT)
    log_type = models.CharField(max_length=32, choices=LOG_TYPE_CHOICES)

    url = models.URLField(max_length=2048)
    status_code = models.IntegerField(default=0, null=True)
    request = models.TextField()
    response = models.TextField(null=True)
    request_time = models.IntegerField()  # how long this request took in milliseconds
    num_retries = models.IntegerField(default=0, null=True)
    created_on = models.DateTimeField(default=timezone.now)

    # whether this was an error which is dependent on the service being called
    is_error = models.BooleanField()

    # foreign keys for fetching logs
    flow = models.ForeignKey(Flow, related_name="http_logs", on_delete=models.PROTECT, null=True)
    classifier = models.ForeignKey(
        Classifier, related_name="http_logs", on_delete=models.PROTECT, db_index=False, null=True
    )
    airtime_transfer = models.ForeignKey(AirtimeTransfer, related_name="http_logs", on_delete=models.PROTECT, null=True)
    channel = models.ForeignKey(Channel, related_name="http_logs", on_delete=models.PROTECT, null=True)

    @classmethod
    def from_response(cls, log_type, response, created_on, ended_on, classifier=None, channel=None):
        """
        Creates a new HTTPLog from an HTTP response
        """
        org = (classifier or channel).org
        http_log = HttpLog.from_response(response, created_on, ended_on)
        is_error = http_log.status_code >= 400

        return cls.objects.create(
            org=org,
            log_type=log_type,
            url=http_log.url,
            request=http_log.request,
            response=http_log.response,
            is_error=is_error,
            created_on=created_on,
            request_time=http_log.elapsed_ms,
            classifier=classifier,
            channel=channel,
        )

    @classmethod
    def from_exception(cls, log_type, exception, created_on, classifier=None, channel=None):
        """
        Creates a new HTTPLog from a request exception (typically a timeout)
        """
        assert isinstance(exception, requests.RequestException)

        org = (classifier or channel).org
        http_log = HttpLog.from_request(exception.request, created_on, timezone.now())

        return cls.objects.create(
            org=org,
            log_type=log_type,
            url=http_log.url,
            request=http_log.request,
            response="",
            is_error=True,
            created_on=created_on,
            request_time=http_log.elapsed_ms,
            channel=channel,
            classifier=classifier,
        )

    def _get_redact_secrets(self) -> tuple:
        if self.channel:
            return self.channel.type.redact_values
        return ()

    def _get_display_value(self, original):
        redact_secrets = self._get_redact_secrets()

        for secret in redact_secrets:
            original = redact.text(original, secret, self.REDACT_MASK)
        return original

    def get_display(self) -> dict:
        return {
            "url": self._get_display_value(self.url),
            "status_code": self.status_code,
            "request": self._get_display_value(self.request),
            "response": self._get_display_value(self.response or ""),
            "elapsed_ms": self.request_time,
            "retries": self.num_retries,
            "created_on": self.created_on.isoformat(),
        }

    class Meta:
        indexes = (
            # for classifier specific log view
            Index(fields=("classifier", "-created_on")),
            # for webhook log view
            Index(name="httplog_org_flows_only", fields=("org", "-created_on"), condition=Q(flow__isnull=False)),
        )
