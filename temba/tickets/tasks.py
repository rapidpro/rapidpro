from temba.utils.crons import cron_task

from .models import TicketCount, TicketDailyCount, TicketDailyTiming


@cron_task(lock_timeout=7200)
def squash_ticket_counts():
    TicketCount.squash()
    TicketDailyCount.squash()
    TicketDailyTiming.squash()
