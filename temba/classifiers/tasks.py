import logging

from temba.utils.celery import nonoverlapping_task

from .models import Classifier

logger = logging.getLogger(__name__)


@nonoverlapping_task(track_started=True, name="sync_classifier_intents", lock_timeout=300)
def sync_classifier_intents(id=None):
    classifiers = Classifier.objects.filter(is_active=True)
    if id:
        classifiers = classifiers.filter(id=id)

    # for each classifier, synchronize to update the intents etc
    for classifier in classifiers:
        try:
            classifier.sync()
        except Exception as e:
            logger.error("error getting intents for classifier", e)
