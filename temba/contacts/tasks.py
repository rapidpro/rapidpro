from __future__ import unicode_literals

from celery.task import task
from temba.utils.queues import nonoverlapping_task
from .models import ExportContactsTask, ContactGroupCount


@task(track_started=True, name='export_contacts_task')
def export_contacts_task(id):
    """
    Export contacts to a file and e-mail a link to the user
    """
    export_task = ExportContactsTask.objects.filter(pk=id).first()
    if export_task:
        export_task.start_export()


@nonoverlapping_task(track_started=True, name='squash_contactgroupcounts')
def squash_contactgroupcounts():
    """
    Squashes our ContactGroupCounts into single rows per ContactGroup
    """
    ContactGroupCount.squash_counts()
