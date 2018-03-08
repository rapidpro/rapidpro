# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import celery
import os
import raven
import sys

from django.conf import settings
from raven.contrib.celery import register_signal, register_logger_signal

# set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'temba.settings')

app = celery.Celery('temba')

app.config_from_object('django.conf:settings')
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)

# register raven if configured
raven_config = getattr(settings, 'RAVEN_CONFIG', None)
if raven_config:  # pragma: no cover
    client = raven.Client(settings.RAVEN_CONFIG['dsn'])
    register_logger_signal(client)
    register_signal(client)


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
