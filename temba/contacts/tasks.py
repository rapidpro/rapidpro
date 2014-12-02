from __future__ import unicode_literals
from datetime import timedelta

from .models import ExportContactsTask
from djcelery_transactions import task

@task(track_started=True, name='export_contacts_task')
def export_contacts_task(id):
    """
    Export contacts to a file and e-mail a link to the user
    """
    tasks = ExportContactsTask.objects.filter(pk=id)
    if tasks:
        task = tasks[0]
        task.do_export()
