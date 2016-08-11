from __future__ import absolute_import, unicode_literals

import time

from datetime import timedelta
from djcelery_transactions import task
from django.utils import timezone
from redis_cache import get_redis_connection
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
def check_credits_task():
    CreditAlert.check_org_credits()


@task(track_started=True, name='calculate_credit_caches')
def calculate_credit_caches():
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
        print " -- recalculated credits for %s in %0.2f seconds" % (org.name, time.time() - start)


@task(track_started=True, name="squash_topupcredits")
def squash_topupcredits():
    r = get_redis_connection()

    key = 'squash_topupcredits'
    if not r.get(key):
        with r.lock(key, timeout=900):
            TopUpCredits.squash_credits()
