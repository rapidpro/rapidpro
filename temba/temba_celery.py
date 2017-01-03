from __future__ import absolute_import

import os
import sys
from celery import Celery
from django.conf import settings

# set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'temba.settings')

app = Celery('temba')

# use django-celery database backend
app.conf.update(
    CELERY_RESULT_BACKEND='djcelery.backends.cache:CacheBackend',
    CELERY_ACCEPT_CONTENT=['json'],
    CELERY_TASK_SERIALIZER='json',
    CELERY_RESULT_SERIALIZER='json',
)

# Using a string here means the worker will not have to
# pickle the object when using Windows.
app.config_from_object('django.conf:settings')
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)


@app.task(bind=True)
def debug_task(self):  # pragma: needs cover
    print('Request: {0!r}'.format(self.request))

# this is needed to simulate CELERY_ALWAYS_EAGER for plain 'send' tasks
if 'test' in sys.argv or getattr(settings, 'CELERY_ALWAYS_EAGER', False):
    from celery import current_app

    def send_task(name, args=(), kwargs={}, **opts):  # pragma: needs cover
        task = current_app.tasks[name]
        return task.apply(args, kwargs, **opts)

    current_app.send_task = send_task
