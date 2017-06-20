from __future__ import absolute_import, print_function, unicode_literals

import time
import requests
import json

from celery.task import task
from datetime import timedelta
from django.utils import timezone
from django.conf import settings
from django.core.cache import cache
from temba.utils.queues import nonoverlapping_task
from .models import CreditAlert, Invitation, Org, TopUpCredits


@task(track_started=True, name='send_invitation_email_task')
def send_invitation_email_task(invitation_id):
    invitation = Invitation.objects.get(pk=invitation_id)
    invitation.send_email()


@task(track_started=True, name='send_alert_email_task')
def send_alert_email_task(alert_id):
    alert = CreditAlert.objects.get(pk=alert_id)
    alert.send_email()


@task(track_started=True, name='check_credits_task')
def check_credits_task():  # pragma: needs cover
    CreditAlert.check_org_credits()


@task(track_started=True, name='calculate_credit_caches')
def calculate_credit_caches():  # pragma: needs cover
    """
    Repopulates the active topup and total credits for each organization
    that received messages in the past week.
    """
    # get all orgs that have sent a message in the past week
    last_week = timezone.now() - timedelta(days=7)

    # for every org that has sent a message in the past week
    for org in Org.objects.filter(msgs__created_on__gte=last_week).distinct('pk'):
        start = time.time()
        org._calculate_credit_caches()
        print(" -- recalculated credits for %s in %0.2f seconds" % (org.name, time.time() - start))


@nonoverlapping_task(track_started=True, name="squash_topupcredits", lock_key='squash_topupcredits')
def squash_topupcredits():
    TopUpCredits.squash()


@task(track_started=True, name='send_chatbase_logs')
def send_chatbase_logs():  # pragma: needs cover
    """
    Send messages logs in batch to Chatbase
    """
    from temba.orgs.models import CHATBASE_API_KEY, CHATBASE_VERSION, ORG_CHATBASE_LOG_CACHE_KEY, CHATBASE_BATCH_SIZE
    from temba.channels.models import TEMBA_HEADERS

    for org in Org.objects.filter(config__icontains=CHATBASE_API_KEY):
        org_chatbase_log_key = ORG_CHATBASE_LOG_CACHE_KEY % org.id
        chatbase_logs = cache.get(org_chatbase_log_key, None)

        if chatbase_logs:
            messages = json.loads(chatbase_logs)
            messages_count = len(messages)

            if messages_count > CHATBASE_BATCH_SIZE:
                count_batch = messages_count / CHATBASE_BATCH_SIZE

            for message in messages:
                message['api_key'] = org.config_json()[CHATBASE_API_KEY]
                if CHATBASE_VERSION in org.config_json():
                    message['version'] = org.config_json()[CHATBASE_VERSION]

            payload = dict(messages=messages)
            payload = json.dumps(payload)

            headers = {'Content-Type': 'application/json'}
            headers.update(TEMBA_HEADERS)
            response = requests.post(settings.CHATBASE_API_URL, data=payload, headers=headers)

            if response.status_code == 200:
                cache.delete(org_chatbase_log_key)
