from __future__ import unicode_literals
from datetime import timedelta

from .models import ExportContactsTask
from djcelery_transactions import task

@task(track_started=True, name='export_contacts_task')
def export_contacts_task(id):
    """
    Export contacts to a file and e-mail a link to the user
    """
    export_task = ExportContactsTask.objects.filter(pk=id).first()
    if export_task:
        export_task.start_export()
