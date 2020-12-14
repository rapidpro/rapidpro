import logging
from abc import ABCMeta

from smartmin.models import SmartModel

from django.conf.urls import url
from django.db import models
from django.template import Engine
from django.utils import timezone

from temba.utils import on_transaction_commit
from temba.utils.models import JSONField
from temba.utils.uuid import uuid4

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

    # the blurb to show on the connect form page
    connect_blurb = None

    def get_connect_blurb(self):
        """
        Gets the blurb for use on the connect page
        """
        return Engine.get_default().from_string(self.connect_blurb)

    def get_form_blurb(self):
        """
        Gets the blurb for use on the connect page
        """
        return Engine.get_default().from_string(self.form_blurb)

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

    def get_active_intents_from_api(self, classifier):
        """
        Should return current set of available intents for the passed in classifier by checking the provider API
        """
        raise NotImplementedError("classifier types must implement get_intents")


class Classifier(SmartModel):
    """
    A classifier represents a set of intents and entity extractors. Many providers call
    these "apps".
    """

    # our uuid
    uuid = models.UUIDField(default=uuid4)

    # the type of this classifier
    classifier_type = models.CharField(max_length=16)

    # the friendly name for this classifier
    name = models.CharField(max_length=255)

    # config values for this classifier
    config = JSONField()

    # the org this classifier is part of
    org = models.ForeignKey("orgs.Org", related_name="classifiers", on_delete=models.PROTECT)

    @classmethod
    def create(cls, org, user, classifier_type, name, config, sync=True):
        classifier = Classifier.objects.create(
            uuid=uuid4(),
            name=name,
            classifier_type=classifier_type,
            config=config,
            org=org,
            created_by=user,
            modified_by=user,
            created_on=timezone.now(),
            modified_on=timezone.now(),
        )

        # trigger a sync of this classifier's intents
        if sync:
            classifier.async_sync()

        return classifier

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

    def sync(self):
        """
        Refresh intents fetches the current intents from the classifier API and updates
        the DB appropriately to match them, inserting logs for all interactions.
        """
        # get the current intents from the API
        intents = self.get_type().get_active_intents_from_api(self)

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
                Intent.objects.create(
                    is_active=True,
                    classifier=self,
                    name=intent.name,
                    external_id=intent.external_id,
                    created_on=timezone.now(),
                )

        # deactivate any intent we haven't seen
        self.intents.filter(is_active=True).exclude(external_id__in=seen).update(is_active=False)

    def async_sync(self):
        """
        Triggers a sync of this classifiers intents
        """
        from .tasks import sync_classifier_intents

        on_transaction_commit(lambda: sync_classifier_intents.delay(self.id))

    def release(self):
        dependent_flows_count = self.dependent_flows.count()
        if dependent_flows_count > 0:
            raise ValueError(f"Cannot delete Classifier: {self.name}, used by {dependent_flows_count} flows")

        # delete our intents
        self.intents.all().delete()

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


class Intent(models.Model):
    """
    Intent represents an intent that a classifier can classify to. It is the job of
    model type implementations to sync these periodically for use in flows etc..
    """

    # intents are forever on an org, but they do get marked inactive when no longer around
    is_active = models.BooleanField(default=True)

    # the classifier this intent is tied to
    classifier = models.ForeignKey(Classifier, related_name="intents", on_delete=models.PROTECT)

    # the name of the intent
    name = models.CharField(max_length=255)

    # the external id of the intent, in same cases this is the same as the name but that is provider specific
    external_id = models.CharField(max_length=255)

    # when we first saw / created this intent
    created_on = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = (("classifier", "external_id"),)
