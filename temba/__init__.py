__version__ = "9.3.51"

# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from .temba_celery import app as celery_app  # noqa
