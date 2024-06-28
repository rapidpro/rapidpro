import logging

from temba.utils.crons import cron_task

from .models import Classifier

logger = logging.getLogger(__name__)


@cron_task(lock_timeout=300)
def sync_classifier_intents(id=None):
    classifiers = Classifier.objects.filter(is_active=True, org__is_active=True, org__is_suspended=False)
    if id:
        classifiers = classifiers.filter(id=id)

    # for each classifier, synchronize to update the intents etc
    for classifier in classifiers:
        try:
            classifier.sync()
        except Exception as e:
            logger.error(f"Error getting intents for classifier: {str(e)}", exc_info=True)
