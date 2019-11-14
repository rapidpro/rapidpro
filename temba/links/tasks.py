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
    if link and contact_id not in [
        item.get("contact__id")
        for item in link.contacts.all().select_related().only("contact__id").values("contact__id")
    ]:
        link_contact_args = dict(
            link=link, contact_id=contact_id, created_by=link.created_by, modified_by=link.modified_by
        )
        LinkContacts.objects.create(**link_contact_args)

        link.clicks_count += 1
        link.save(update_fields=["clicks_count"])
