# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from celery.task import task
from django_redis import get_redis_connection
from temba.utils.queues import nonoverlapping_task
from .models import ExportContactsTask, ContactGroupCount, ContactGroup


@task(track_started=True, name='export_contacts_task')
def export_contacts_task(task_id):
    """
    Export contacts to a file and e-mail a link to the user
    """
    ExportContactsTask.objects.get(id=task_id).perform()


@nonoverlapping_task(track_started=True, name='squash_contactgroupcounts')
def squash_contactgroupcounts():
    """
    Squashes our ContactGroupCounts into single rows per ContactGroup
    """
    ContactGroupCount.squash()


@task(track_started=True, name='reevaluate_dynamic_group')
def reevaluate_dynamic_group(group_id):
    """
    (Re)evaluate a dynamic group
    """
    r = get_redis_connection()
    lock_key = ContactGroup.REEVALUATE_LOCK_KEY % group_id

    with r.lock(lock_key, 3600):
        ContactGroup.user_groups.get(id=group_id).reevaluate()
