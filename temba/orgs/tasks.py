from .models import CreditAlert, Invitation
from djcelery_transactions import task
from django.conf import settings

@task(track_started=True, name='send_invitation_email_task')
def send_invitation_email_task(invitation_id):
    invitation = Invitation.objects.get(pk=invitation_id)
    invitation.send_email()

@task(track_started=True, name='check_credits_task')
def check_credits_task():
    CreditAlert.check_org_credits()
