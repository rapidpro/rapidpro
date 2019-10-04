import logging

from temba.utils.celery import nonoverlapping_task

from .models import Classifier

logger = logging.getLogger(__name__)


@nonoverlapping_task(track_started=True, name="sync_classifier_intents", lock_timeout=300)
def sync_classifier_intents(id=None):
    classifiers = Classifier.objects.filter(is_active=True)
    if id:
        classifiers = classifiers.filter(id=id)

    # for each classifier, refresh our intents
    for classifier in classifiers:
        classifier.refresh_intents()
