# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from .temba_celery import app as celery_app  # noqa
