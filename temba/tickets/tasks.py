from django.db.models import Prefetch

from celery import shared_task

from temba.contacts.models import ContactField, ContactGroup
from temba.utils.celery import nonoverlapping_task

from .models import ExportTicketsTask, TicketCount, TicketDailyCount, TicketDailyTiming


@shared_task(track_started=True, name="export_tickets_task")
def export_tickets_task(task_id):
    """
    Export tickets to a file and email a link to the user
    """
    ExportTicketsTask.objects.select_related("org", "created_by").prefetch_related(
        Prefetch("with_fields", ContactField.objects.order_by("name")),
        Prefetch("with_groups", ContactGroup.objects.order_by("name")),
    ).get(id=task_id).perform()


@nonoverlapping_task(track_started=True, name="squash_ticketcounts", lock_timeout=7200)
def squash_ticketcounts():
    TicketCount.squash()
    TicketDailyCount.squash()
    TicketDailyTiming.squash()
