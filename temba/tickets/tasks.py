from temba.utils.celery import nonoverlapping_task

from .models import TicketCount


@nonoverlapping_task(track_started=True, lock_timeout=7200)
def squash_ticketcounts():
    TicketCount.squash()
