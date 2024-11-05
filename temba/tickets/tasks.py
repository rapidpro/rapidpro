from temba.utils.crons import cron_task

from .models import TicketDailyCount, TicketDailyTiming


@cron_task(lock_timeout=7200)
def squash_ticket_counts():
    TicketDailyCount.squash()
    TicketDailyTiming.squash()
