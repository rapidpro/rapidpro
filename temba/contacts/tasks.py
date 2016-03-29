from __future__ import unicode_literals

from djcelery_transactions import task
from redis_cache import get_redis_connection
from .models import ExportContactsTask, ContactGroupCount


@task(track_started=True, name='export_contacts_task')
def export_contacts_task(id):
    """
    Export contacts to a file and e-mail a link to the user
    """
    export_task = ExportContactsTask.objects.filter(pk=id).first()
    if export_task:
        export_task.start_export()


@task(track_started=True, name='squash_contactgroupcounts')
def squash_contactgroupcounts():
    """
    Squashes our ContactGroupCounts into single rows per ContactGroup
    """
    r = get_redis_connection()

    key = 'squash_channelcounts'
    if not r.get(key):
        with r.lock(key, timeout=900):
            ContactGroupCount.squash_counts()
