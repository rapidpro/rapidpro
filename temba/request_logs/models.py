import logging
from datetime import timedelta

from requests_toolbelt.utils import dump

from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.orgs.models import Org
from temba.utils import chunk_list

logger = logging.getLogger(__name__)


class HTTPLog(models.Model):
    """
    HTTPLog is used to log HTTP requests and responses.
    """

    REQUEST_DELIM = ">!>!>! "
    RESPONSE_DELIM = "<!<!<! "

    # log type choices
    INTENTS_SYNCED = "intents_synced"
    CLASSIFIER_CALLED = "classifier_called"
    AIRTIME_TRANSFERRED = "airtime_transferred"
    WHATSAPP_TEMPLATES_SYNCED = "whatsapp_templates_synced"
    WHATSAPP_TOKENS_SYNCED = "whatsapp_tokens_synced"
    WHATSAPP_CONTACTS_REFRESHED = "whatsapp_contacts_refreshed"

    # possible log type choices and descriptive names
    LOG_TYPE_CHOICES = (
        (INTENTS_SYNCED, _("Intents Synced")),
        (CLASSIFIER_CALLED, _("Classifier Called")),
        (AIRTIME_TRANSFERRED, _("Airtime Transferred")),
        (WHATSAPP_TEMPLATES_SYNCED, _("WhatsApp Templates Synced")),
        (WHATSAPP_TOKENS_SYNCED, _("WhatsApp Tokens Synced")),
        (WHATSAPP_CONTACTS_REFRESHED, _("WhatsApp Contacts Refreshed")),
    )

    # the classifier this log is for
    classifier = models.ForeignKey(
        "classifiers.Classifier", related_name="http_logs", on_delete=models.PROTECT, db_index=False, null=True
    )

    # the airtime transfer this log is for
    airtime_transfer = models.ForeignKey(
        "airtime.AirtimeTransfer", related_name="http_logs", on_delete=models.PROTECT, null=True
    )

    # the channel this log is for
    channel = models.ForeignKey("channels.Channel", related_name="http_logs", on_delete=models.PROTECT, null=True)

    # the type of log this is
    log_type = models.CharField(max_length=32, choices=LOG_TYPE_CHOICES)

    # the url that was called
    url = models.URLField()

    # the request that was made
    request = models.TextField()

    # the response received
    response = models.TextField(null=True)

    # whether this was an error
    is_error = models.BooleanField()

    # how long this request took in milliseconds
    request_time = models.IntegerField()

    # when this was created
    created_on = models.DateTimeField(default=timezone.now)

    # the org this log is part of
    org = models.ForeignKey(Org, related_name="http_logs", on_delete=models.PROTECT)

    class Meta:
        index_together = (("classifier", "created_on"),)

    def method(self):
        return self.request.split(" ")[0] if self.request else None

    def status_code(self):
        return self.response.split(" ")[1] if self.response else None

    def release(self):
        self.delete()

    @classmethod
    def trim(cls):
        """
        Deletes all HTTP Logs older than 3 days, 1000 at a time
        """
        cutoff = timezone.now() - timedelta(days=3)
        ids = HTTPLog.objects.filter(created_on__lte=cutoff).values_list("id", flat=True)
        for chunk in chunk_list(ids, 1000):
            HTTPLog.objects.filter(id__in=chunk).delete()

    @classmethod
    def create_from_response(cls, log_type, url, response, classifier=None, channel=None, request_time=None):
        if classifier is not None:
            org = classifier.org

        if channel is not None:
            org = channel.org

        is_error = response.status_code != 200
        data = dump.dump_response(
            response, request_prefix=cls.REQUEST_DELIM, response_prefix=cls.RESPONSE_DELIM
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

        return HTTPLog.objects.create(
            classifier=classifier,
            channel=channel,
            log_type=log_type,
            url=url,
            request=request,
            response=response,
            is_error=is_error,
            created_on=timezone.now(),
            request_time=request_time,
            org=org,
        )

    @classmethod
    def create_from_exception(cls, log_type, url, exception, start, classifier=None, channel=None):
        if classifier is not None:
            org = classifier.org

        if channel is not None:
            org = channel.org

        data = bytearray()
        prefixes = dump.PrefixSettings(cls.REQUEST_DELIM, cls.RESPONSE_DELIM)
        dump._dump_request_data(exception.request, prefixes, data)

        data = data.decode("utf-8")
        request_lines = data.split(cls.REQUEST_DELIM)
        request = "".join(request_lines)

        return HTTPLog.objects.create(
            channel=channel,
            classifier=classifier,
            log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED,
            url=url,
            request=request,
            response="",
            is_error=True,
            created_on=timezone.now(),
            request_time=(timezone.now() - start).total_seconds() * 1000,
            org=org,
        )
