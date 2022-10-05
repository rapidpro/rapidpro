from celery import shared_task

from temba.utils.celery import nonoverlapping_task

from .models import ExportTicketsTask, TicketCount, TicketDailyCount, TicketDailyTiming


@shared_task(track_started=True, name="export_tickets_task")
def export_tickets_task(task_id):
    """
    Export tickets to a file and email a link to the user
    """
    ExportTicketsTask.objects.select_related("org", "created_by").get(id=task_id).perform()


@nonoverlapping_task(track_started=True, name="squash_ticketcounts", lock_timeout=7200)
def squash_ticketcounts():
    TicketCount.squash()
    TicketDailyCount.squash()
    TicketDailyTiming.squash()
