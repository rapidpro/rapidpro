import logging
from django.db import models
from temba.utils.models import TembaModel, JSONField, generate_uuid
from django.conf.urls import url
from django.utils import timezone
from abc import ABCMeta
from django.template import Engine
from requests_toolbelt.utils import dump

logger = logging.getLogger(__name__)


class ClassifierType(metaclass=ABCMeta):
    """
    ClassifierType is our abstract base type for custom NLU providers. Each provider will
    supply a way of connecting a new classifier as well as a way of getting current intents. The job
    of running classifiers and extracting entities will be done by type specific implementations in
    GoFlow and Mailroom.
    """

    # the verbose name for this classifier type
    name = None

    # the short code for this classifier type (< 16 chars, lowercase)
    slug = None

    # the icon to show for this classifier type
    icon = "icon-channel-external"

    # the blurb to show on the main connect page
    connect_blurb = None

    # the view that handles connection of a new model
    connect_view = None

    def get_connect_blurb(self):
        """
        Gets the blurb for use on the connect page
        """
        return Engine.get_default().from_string(self.connect_blurb)

    def get_urls(self):
        """
        Returns all the URLs this classifier exposes to Django, the URL should be relative.
        """
        return [self.get_connect_url()]

    def get_connect_url(self):
        """
        Gets the URL/view configuration for this classifier's connect page
        """
        return url(r"^connect", self.connect_view.as_view(classifier_type=self), name="connect")

    @classmethod
    def get_active_intents_from_api(cls, classifier):
        """
        Should return current set of available intents for the passed in classifier by checking the provider API
        """
        raise NotImplementedError("classifier types must implement get_intents")


class Classifier(TembaModel):
    """
    A classifier represents a set of intents and entity extractors. Many providers call
    these "apps".
    """

    # the type of this classifier
    classifier_type = models.CharField(max_length=16, null=False)

    # the friendly name for this classifier
    name = models.CharField(max_length=255, null=False)

    # config values for this classifier
    config = JSONField(null=False)

    # the org this classifier is part of
    org = models.ForeignKey("orgs.Org", null=False, on_delete=models.PROTECT)

    def get_type(self):
        """
        Returns the type instance for this classifier
        """
        from .types import TYPES

        return TYPES[self.classifier_type]

    def active_intents(self):
        """
        Returns the list of active intents on this classifier
        """
        return self.intents.filter(is_active=True).order_by("name")

    def refresh_intents(self):
        """
        Refresh intents fetches the current intents from the classifier API and updates
        the DB appropriately to match them, inserting logs for all interactions.
        """
        # get the current intents from the API
        logs = []
        intents = None

        try:
            intents = self.get_type().get_active_intents_from_api(self, logs)
        except Exception as e:
            logger.error("error getting intents for classifier", e)

        # insert our logs
        for log in logs:
            log.save()

        # if we were returned None as intents, then we had an error, log but don't actually update our intents
        if intents is None:
            return

        # external ids we have seen
        seen = []

        # for each intent
        for intent in intents:
            assert intent.external_id is not None
            assert intent.name != "" and intent.name is not None

            seen.append(intent.external_id)

            existing = self.intents.filter(external_id=intent.external_id).first()
            if existing:
                # previously existed, reactive it
                if not existing.is_active:
                    existing.is_active = True
                    existing.save(update_fields=["is_active"])

            elif not existing:
                existing = Intent.objects.create(
                    is_active=True,
                    classifier=self,
                    name=intent.name,
                    external_id=intent.external_id,
                    created_on=timezone.now(),
                )

        # deactivate any intent we haven't seen
        self.intents.filter(is_active=True).exclude(external_id__in=seen).update(is_active=False)

    def release(self):
        dependent_flows_count = self.dependent_flows.count()
        if dependent_flows_count > 0:
            raise ValueError(f"Cannot delete Classifier: {self.name}, used by {dependent_flows_count} flows")

        self.is_active = False
        self.save(update_fields=["is_active"])

    @classmethod
    def get_types(cls):
        """
        Returns the possible types available for classifiers
        :return:
        """
        from .types import TYPES

        return TYPES.values()

    @classmethod
    def create(cls, org, user, classifier_type, name, config):
        return Classifier.objects.create(
            uuid=generate_uuid(),
            name=name,
            classifier_type=classifier_type,
            config=config,
            org=org,
            created_by=user,
            modified_by=user,
            created_on=timezone.now(),
            modified_on=timezone.now(),
        )


class Intent(models.Model):
    """
    Intent represents an intent that a classifier can classify to. It is the job of
    model type implementations to sync these periodically for use in flows etc..
    """

    # intents are forever on an org, but they do get marked inactive when no longer around
    is_active = models.BooleanField(null=False, default=True)

    # the classifier this intent is tied to
    classifier = models.ForeignKey(Classifier, related_name="intents", on_delete=models.PROTECT)

    # the name of the intent
    name = models.CharField(max_length=255, null=False)

    # the external id of the intent, in same cases this is the same as the name but that is provider specific
    external_id = models.CharField(max_length=255, null=False)

    # when we first saw / created this intent
    created_on = models.DateTimeField(null=False, default=timezone.now)

    class Meta:
        unique_together = (("classifier", "external_id"),)


class ClassifierLog(models.Model):
    """
    ClassifierLog is used to log requests and responses with a classifier. This includes both flow classifications
    and intent syncing events.
    """

    # the classifier this log is for
    classifier = models.ForeignKey(Classifier, null=False, related_name="logs", on_delete=models.PROTECT)

    # the url that was called
    url = models.URLField(null=False)

    # the request that was made
    request = models.TextField(null=False)

    # the response received
    response = models.TextField(null=False)

    # whether this was an error
    is_error = models.BooleanField(null=False)

    # a short description of the result
    description = models.CharField(max_length=255, null=False)

    # how long this request took in milliseconds
    request_time = models.IntegerField(null=False)

    # when this was created
    created_on = models.DateTimeField(null=False, default=timezone.now)

    def method(self):
        return self.request.split(" ")[0] if self.request else None

    def status_code(self):
        return self.response.split(" ")[1] if self.response else None

    @classmethod
    def from_response(cls, classifier, url, response, success_desc, failure_desc):
        is_error = response.status_code != 200
        description = failure_desc if is_error else success_desc

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

        return ClassifierLog(
            classifier=classifier,
            url=url,
            request=request,
            response=response,
            is_error=is_error,
            description=description,
            created_on=timezone.now(),
        )
