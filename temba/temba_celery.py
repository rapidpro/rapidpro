import os

import celery

from django.conf import settings

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "temba.settings")


class TembaCelery(celery.Celery):
    def gen_task_name(self, name, module):
        """
        Just use func name for task name
        """
        return name


app = TembaCelery("temba")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)
app.autodiscover_tasks(("temba.channels.types.twitter", "temba.channels.types.whatsapp_legacy"))
