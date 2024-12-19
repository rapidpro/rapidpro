import itertools
import logging

from celery import shared_task

from temba.orgs.models import User
from temba.utils.crons import cron_task

from .models import Contact, ContactGroup, ContactGroupCount, ContactImport

logger = logging.getLogger(__name__)


@shared_task
def release_contacts(user_id, contact_ids):
    """
    Releases the given contacts
    """
    user = User.objects.get(pk=user_id)

    for id_batch in itertools.batched(contact_ids, 100):
        batch = Contact.objects.filter(id__in=id_batch, is_active=True).prefetch_related("urns")
        for contact in batch:
            contact.release(user)


@shared_task
def import_contacts_task(import_id):
    """
    Import contacts from a spreadsheet
    """
    ContactImport.objects.select_related("org", "created_by").get(id=import_id).start()


@shared_task
def release_group_task(group_id):
    """
    Releases group
    """
    ContactGroup.objects.get(id=group_id)._full_release()


@cron_task(lock_timeout=7200)
def squash_group_counts():
    """
    Squashes our ContactGroupCounts into single rows per ContactGroup
    """
    ContactGroupCount.squash()


@shared_task
def full_release_contact(contact_id):
    contact = Contact.objects.filter(id=contact_id).first()

    if contact and not contact.is_active:
        contact._full_release()
