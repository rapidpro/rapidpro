# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from celery.task import task
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
    (Re)build a dynamic group
    """
    ContactGroup.user_groups.get(id=group_id).reevaluate()
