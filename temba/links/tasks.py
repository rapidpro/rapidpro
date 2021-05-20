import logging

from celery.task import task

from .models import Link, LinkContacts, ExportLinksTask

logger = logging.getLogger(__name__)


@task(track_started=True, name="export_link_task")
def export_link_task(id):
    """
    Export link contacts to a file and e-mail a link to the user
    """
    ExportLinksTask.objects.get(id=id).perform()


@task(track_started=True, name="handle_link_task")
def handle_link_task(link_id, contact_id):
    link = Link.objects.filter(pk=link_id).only("created_by", "modified_by").first()
    if link and contact_id:
        # to count unique clicks
        LinkContacts.objects.get_or_create(
            link_id=link.id, contact_id=contact_id, created_by=link.created_by, modified_by=link.modified_by
        )
        # to count all clicks
        link.clicks_count += 1
        link.save(update_fields=["clicks_count"])
