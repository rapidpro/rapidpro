import logging

from requests_toolbelt.utils import dump

from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.orgs.models import Org

logger = logging.getLogger(__name__)


class HTTPLog(models.Model):
    """
    HTTPLog is used to log HTTP requests and responses.
    """

    # classifier type choices
    INTENTS_SYNCED = "intents_synced"
    CLASSIFIER_CALLED = "classifier_called"

    # possible log type choices and descriptive names
    LOG_TYPE_CHOICES = ((INTENTS_SYNCED, _("Intents Synced")), (CLASSIFIER_CALLED, _("Classifier Called")))

    # the classifier this log is for
    classifier = models.ForeignKey(
        "classifiers.Classifier", related_name="http_logs", on_delete=models.PROTECT, db_index=False
    )

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

    @classmethod
    def from_response(cls, log_type, url, response, classifier=None):
        # remove once we have other types
        assert classifier is not None

        if classifier is not None:
            org = classifier.org

        is_error = response.status_code != 200
        data = dump.dump_response(response, request_prefix=">>> ", response_prefix="<<< ").decode("utf-8")

        # split by lines
        lines = data.split("\r\n")
        request_lines = []
        response_lines = []

        for line in lines:
            if line.startswith(">>> "):
                request_lines.append(line[4:])
            else:
                response_lines.append(line[4:])

        request = "\r\n".join(request_lines)
        response = "\r\n".join(response_lines)

        return HTTPLog(
            classifier=classifier,
            log_type=log_type,
            url=url,
            request=request,
            response=response,
            is_error=is_error,
            created_on=timezone.now(),
            org=org,
        )
