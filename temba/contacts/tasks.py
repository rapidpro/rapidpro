# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
from datetime import timedelta

import iso8601
import pytz
from celery.task import task
from django.conf import settings

from temba.utils.queues import nonoverlapping_task
from .models import ExportContactsTask, ContactGroupCount, ContactGroup, Contact

logger = logging.getLogger(__name__)


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
    ContactGroup.user_groups.get(id=group_id).reevaluate()


@task(name='check_elasticsearch_lag')
def check_elasticsearch_lag():
    if settings.ELASTICSEARCH_URL:
        from temba.utils.es import ES, ModelESSearch

        # get the modified_on of the last synced contact
        res = (
            ModelESSearch(model=Contact, index='contacts')
            .params(size=1)
            .sort('-modified_on_mu')
            .source(include=['modified_on'])
            .using(ES)
            .execute()
        )

        if res['hits']['hits']:
            es_contact = res['hits']['hits'][0]
            db_contact = Contact.objects.filter(is_test=False).order_by('-modified_on').first()

            if db_contact:
                es_modified_on = iso8601.parse_date(es_contact['_source']['modified_on'], pytz.utc)

                # check the lag between the two, shouldn't be more than 5 minutes
                if db_contact.modified_on - es_modified_on > timedelta(minutes=5):
                    logger.error("drift between ElasticSearch and DB. Newest DB: %s, Newest ES: %s",
                                 db_contact.modified_on, es_modified_on)

                    return False

    return True
