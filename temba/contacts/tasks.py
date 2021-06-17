import logging
from datetime import timedelta

import iso8601
import pytz

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from celery.task import task

from temba.utils import chunk_list
from temba.utils.celery import nonoverlapping_task

from .models import Contact, ContactGroup, ContactGroupCount, ContactImport, ExportContactsTask
from .search import elastic

logger = logging.getLogger(__name__)


@task(track_started=True)
def release_contacts(user_id, contact_ids):
    """
    Releases the given contacts
    """
    user = User.objects.get(pk=user_id)

    for id_batch in chunk_list(contact_ids, 100):
        batch = Contact.objects.filter(id__in=id_batch, is_active=True).prefetch_related("urns")
        for contact in batch:
            contact.release(user)


@task(track_started=True)
def import_contacts_task(import_id):
    """
    Import contacts from a spreadsheet
    """
    ContactImport.objects.get(id=import_id).start()


@task(track_started=True, name="export_contacts_task")
def export_contacts_task(task_id):
    """
    Export contacts to a file and e-mail a link to the user
    """
    ExportContactsTask.objects.get(id=task_id).perform()


@nonoverlapping_task(track_started=True, name="release_group_task")
def release_group_task(group_id):
    """
    Releases group
    """
    ContactGroup.all_groups.get(id=group_id).release()


@nonoverlapping_task(track_started=True, name="squash_contactgroupcounts", lock_timeout=7200)
def squash_contactgroupcounts():
    """
    Squashes our ContactGroupCounts into single rows per ContactGroup
    """
    ContactGroupCount.squash()


@task(track_started=True, name="full_release_contact")
def full_release_contact(contact_id):
    contact = Contact.objects.filter(id=contact_id).first()

    if contact and not contact.is_active:
        contact._full_release()


@task(name="check_elasticsearch_lag")
def check_elasticsearch_lag():
    if settings.ELASTICSEARCH_URL:
        es_last_modified_contact = elastic.get_last_modified()

        if es_last_modified_contact:
            # if we have elastic results, make sure they aren't more than five minutes behind
            db_contact = Contact.objects.order_by("-modified_on").first()
            es_modified_on = iso8601.parse_date(es_last_modified_contact["modified_on"], pytz.utc)
            es_id = es_last_modified_contact["id"]

            # no db contact is an error, ES should be empty as well
            if not db_contact:
                logger.error(
                    "db empty but ElasticSearch has contacts. Newest ES(id: %d, modified_on: %s)",
                    es_id,
                    es_modified_on,
                )
                return False

            #  check the lag between the two, shouldn't be more than 5 minutes
            if db_contact.modified_on - es_modified_on > timedelta(minutes=5):
                logger.error(
                    "drift between ElasticSearch and DB. Newest DB(id: %d, modified_on: %s) Newest ES(id: %d, modified_on: %s)",
                    db_contact.id,
                    db_contact.modified_on,
                    es_id,
                    es_modified_on,
                )

                return False

        else:
            # we don't have any ES hits, get our oldest db contact, check it is less than five minutes old
            db_contact = Contact.objects.order_by("modified_on").first()
            if db_contact and timezone.now() - db_contact.modified_on > timedelta(minutes=5):
                logger.error(
                    "ElasticSearch empty with DB contacts older than five minutes. Oldest DB(id: %d, modified_on: %s)",
                    db_contact.id,
                    db_contact.modified_on,
                )

                return False

    return True
