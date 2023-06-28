__version__ = "8.1.211"  # set by poetry bumpversion plugin

# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from .temba_celery import app as celery_app  # noqa
