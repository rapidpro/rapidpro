import logging

from requests_toolbelt.utils import dump

from django.db import models
from django.db.models import Index, Q
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _

from temba.airtime.models import AirtimeTransfer
from temba.channels.models import Channel
from temba.classifiers.models import Classifier
from temba.flows.models import Flow
from temba.orgs.models import Org
from temba.tickets.models import Ticketer

logger = logging.getLogger(__name__)


class HTTPLog(models.Model):
    """
    HTTPLog is used to log HTTP requests and responses.
    """

    # used for dumping traces
    REQUEST_DELIM = ">!>!>! "
    RESPONSE_DELIM = "<!<!<! "

    # log type choices
    WEBHOOK_CALLED = "webhook_called"
    INTENTS_SYNCED = "intents_synced"
    CLASSIFIER_CALLED = "classifier_called"
    TICKETER_CALLED = "ticketer_called"
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
        (TICKETER_CALLED, _("Ticketing Service Called")),
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
    ticketer = models.ForeignKey(
        Ticketer, related_name="http_logs", on_delete=models.PROTECT, db_index=False, null=True
    )
    airtime_transfer = models.ForeignKey(
        AirtimeTransfer, related_name="http_logs", on_delete=models.PROTECT, null=True
    )
    channel = models.ForeignKey(Channel, related_name="http_logs", on_delete=models.PROTECT, null=True)

    @cached_property
    def method(self):
        return self.request.split(" ")[0] if self.request else None

    @classmethod
    def create_from_response(
        cls, log_type, url, response, classifier=None, channel=None, ticketer=None, request_time=None
    ):
        org = (classifier or channel or ticketer).org

        is_error = response.status_code >= 400
        data = dump.dump_response(
            response,
            request_prefix=cls.REQUEST_DELIM.encode("utf-8"),
            response_prefix=cls.RESPONSE_DELIM.encode("utf-8"),
        ).decode("utf-8")

        # first build our array of request lines, our last item will also contain our response lines
        request_lines = data.split(cls.REQUEST_DELIM)

        # now split our response lines from the last request line
        response_lines = request_lines[-1].split(cls.RESPONSE_DELIM)

        # and clean up the last and first item appropriately
        request_lines[-1] = response_lines[0]
        response_lines = response_lines[1:]

        request = "".join(request_lines)
        response = "".join(response_lines)

        return cls.objects.create(
            org=org,
            log_type=log_type,
            url=url,
            request=request,
            response=response,
            is_error=is_error,
            created_on=timezone.now(),
            request_time=request_time,
            classifier=classifier,
            channel=channel,
            ticketer=ticketer,
        )

    @classmethod
    def create_from_exception(cls, log_type, url, exception, start, classifier=None, channel=None, ticketer=None):
        org = (classifier or channel or ticketer).org

        data = bytearray()
        prefixes = dump.PrefixSettings(cls.REQUEST_DELIM, cls.RESPONSE_DELIM)
        dump._dump_request_data(exception.request, prefixes, data)

        data = data.decode("utf-8")
        request_lines = data.split(cls.REQUEST_DELIM)
        request = "".join(request_lines)

        return cls.objects.create(
            org=org,
            log_type=log_type,
            url=url,
            request=request,
            response="",
            is_error=True,
            created_on=timezone.now(),
            request_time=(timezone.now() - start).total_seconds() * 1000,
            channel=channel,
            classifier=classifier,
            ticketer=ticketer,
        )

    class Meta:
        indexes = (
            # for classifier specific log view
            Index(fields=("classifier", "-created_on")),
            # for webhook log view
            Index(name="httplog_org_flows_only", fields=("org", "-created_on"), condition=Q(flow__isnull=False)),
            # for ticketer specific log view
            Index(fields=("ticketer", "-created_on")),
        )
