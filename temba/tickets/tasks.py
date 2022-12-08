from celery import shared_task

from django.db.models import Prefetch

from temba.contacts.models import ContactField, ContactGroup
from temba.utils.crons import cron_task

from .models import ExportTicketsTask, TicketCount, TicketDailyCount, TicketDailyTiming


@shared_task
def export_tickets_task(task_id):
    """
    Export tickets to a file and email a link to the user
    """
    ExportTicketsTask.objects.select_related("org", "created_by").prefetch_related(
        Prefetch("with_fields", ContactField.objects.order_by("name")),
        Prefetch("with_groups", ContactGroup.objects.order_by("name")),
    ).get(id=task_id).perform()


@cron_task(lock_timeout=7200)
def squash_ticket_counts():
    TicketCount.squash()
    TicketDailyCount.squash()
    TicketDailyTiming.squash()
