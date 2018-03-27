# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from celery.task import task
from temba.utils.queues import nonoverlapping_task
from .models import CreditAlert, Invitation, TopUpCredits, Org


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


@task(track_started=True, name='apply_topups_task')
def apply_topups_task(org_id):
    org = Org.objects.get(id=org_id)
    org.apply_topups()
    org.trigger_send()


@nonoverlapping_task(track_started=True, name="squash_topupcredits", lock_key='squash_topupcredits')
def squash_topupcredits():
    TopUpCredits.squash()
