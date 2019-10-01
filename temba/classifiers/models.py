from django.db import models
from temba.utils.models import TembaModel, JSONField, generate_uuid
from django.conf.urls import url
from django.utils import timezone
from abc import ABCMeta
from django.template import Engine

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

    def get_current_intents(self):
        """
        Should return current set of available intents for the classifier
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

    @classmethod
    def get_types(cls):
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

    is_active = models.BooleanField(null=False, default=True)

    classifier = models.ForeignKey(Classifier, on_delete=models.PROTECT)

    uuid = models.UUIDField(null=False)

    name = models.CharField(max_length=64, null=False)

    config = JSONField(null=False)

    created_on = models.DateTimeField(null=False, default=timezone.now)