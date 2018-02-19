# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from celery.task import task
from .models import IVRCall


@task(name="start_call_task")
def start_call_task(call_pk):
    call = IVRCall.objects.get(pk=call_pk)
    call.do_start_call()
