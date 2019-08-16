import calendar
import logging
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from smartmin.models import SmartModel

from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

logger = logging.getLogger(__name__)


class Schedule(SmartModel):
    """
    Describes a point in the future to execute some action. These are used to schedule Broadcasts
    as a single event or with a specified interval for recurrence.
    """

    REPEAT_NEVER = "O"
    REPEAT_DAILY = "D"
    REPEAT_WEEKLY = "W"
    REPEAT_MONTHLY = "M"
    REPEAT_CHOICES = (
        (REPEAT_NEVER, _("Never")),
        (REPEAT_DAILY, _("Daily")),
        (REPEAT_WEEKLY, _("Weekly")),
        (REPEAT_MONTHLY, _("Monthly")),
    )

    STATUS_CHOICES = (("U", "Unscheduled"), ("S", "Scheduled"))

    status = models.CharField(default="U", choices=STATUS_CHOICES, max_length=1)
    repeat_hour_of_day = models.IntegerField(help_text="The hour of the day", null=True)
    repeat_minute_of_hour = models.IntegerField(help_text="The minute of the hour", null=True)
    repeat_day_of_month = models.IntegerField(null=True, help_text="The day of the month to repeat on")
    repeat_period = models.CharField(
        max_length=1, null=True, help_text="When this schedule repeats", choices=REPEAT_CHOICES
    )
    repeat_days = models.IntegerField(default=0, null=True, blank=True, help_text="bit mask of days of the week")
    last_fire = models.DateTimeField(null=True, blank=True, default=None, help_text="When this schedule last fired")
    next_fire = models.DateTimeField(null=True, blank=True, default=None, help_text="When this schedule fires next")

    @classmethod
    def create_schedule(cls, start_date, repeat_period, user, repeat_days=None, status="S"):
        return Schedule.objects.create(
            repeat_period=repeat_period,
            repeat_days=repeat_days,
            created_by=user,
            modified_by=user,
            repeat_day_of_month=start_date.day,
            repeat_hour_of_day=start_date.hour,
            repeat_minute_of_hour=start_date.minute,
            next_fire=start_date,
            status=status,
        )

    def reset(self):
        self.next_fire = None
        self.status = "U"
        self.repeat_period = Schedule.REPEAT_NEVER
        self.repeat_days = 0
        self.save()

    def get_broadcast(self):
        if hasattr(self, "broadcast"):
            return self.broadcast

    def get_trigger(self):
        if hasattr(self, "trigger"):
            return self.trigger

    def get_org_timezone(self):
        org = None

        if self.get_broadcast():  # pragma: needs cover
            org = self.get_broadcast().org

        if org and org.timezone:  # pragma: needs cover
            return org.timezone
        else:
            return timezone.pytz.utc

    def get_next_fire(self, trigger_date):
        """
        Get the next point in the future when our schedule should fire again
        """
        hour = self.repeat_hour_of_day if self.repeat_hour_of_day is not None else trigger_date.hour
        minute = self.repeat_minute_of_hour if self.repeat_minute_of_hour is not None else 0

        trigger_date = trigger_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if self.repeat_period == Schedule.REPEAT_NEVER:
            return trigger_date

        if self.repeat_period == Schedule.REPEAT_MONTHLY:
            (weekday, days) = calendar.monthrange(trigger_date.year, trigger_date.month)
            day_of_month = min(days, self.repeat_day_of_month)
            next_date = datetime(
                trigger_date.year,
                trigger_date.month,
                day=day_of_month,
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            next_date = self.get_org_timezone().localize(next_date)
            if trigger_date.day >= day_of_month:
                next_date += relativedelta(months=1)
                (weekday, days) = calendar.monthrange(next_date.year, next_date.month)
                day_of_month = min(days, self.repeat_day_of_month)
                next_date = next_date.replace(day=day_of_month)

            return next_date

        if self.repeat_period == Schedule.REPEAT_WEEKLY:
            # find the next day we are to repeat
            if self.repeat_days:
                dow = trigger_date.weekday()
                for i in range(7):
                    # add one so we start with tomorrow
                    day_idx = (dow + i + 1) % 7

                    # 2-128 bitmask for encoding the days of the week
                    # use base-1 when calculating our powers of 2
                    bitmask = pow(2, day_idx + 1)
                    if bitmask & self.repeat_days == bitmask:
                        return trigger_date + timedelta(days=i + 1)

        if self.repeat_period == Schedule.REPEAT_DAILY:
            return trigger_date + timedelta(days=1)

    def update_schedule(self, now=None):
        """
        Updates our schedule for the next date, returns true if it was expired
        """

        if not now:
            now = timezone.now()

        if self.is_expired() and now:
            self.next_fire = self.get_next_fire(now)
            self.last_fire = now
            self.save()
            return True

    def is_expired(self):
        if self.next_fire:
            next_fire = self.next_fire
            return next_fire < timezone.now()
        else:
            return False

    def has_pending_fire(self):
        if self.status == "S" and self.next_fire and self.next_fire > timezone.now():
            return True

    def fire(self):
        broadcast = self.get_broadcast()
        trigger = self.get_trigger()

        logger.info(f"Firing {str(self)}")

        if broadcast:
            broadcast.fire()

        elif trigger:
            trigger.fire()

        else:
            logger.error("Tried to fire schedule but it wasn't attached to anything", extra={"schedule_id": self.id})

        # if its one time, delete our schedule
        if self.repeat_period == Schedule.REPEAT_NEVER:
            self.reset()

    def unschedule(self):
        logger.info(f"Unscheduling {str(self)}")

        self.status = "U"
        self.save(update_fields=("status",))

    def explode_bitmask(self):
        if self.repeat_days:
            bitmask_number = bin(self.repeat_days)
            days = []
            for idx in range(7):
                power = bin(pow(2, idx + 1))
                if bin(int(bitmask_number, 2) & int(power, 2)) == power:
                    days.append(idx)
            return days
        return []

    def get_repeat_days_display(self):
        dow = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        days = self.explode_bitmask()
        for i in range(len(days)):
            days[i] = dow[days[i]]
        return days

    def __str__(self):
        repeat = (
            f"{self.repeat_period} {self.repeat_day_of_month} {self.repeat_hour_of_day}:{self.repeat_minute_of_hour}"
        )
        return f"schedule[id={self.id} repeat={repeat} next={str(self.next_fire)}]"
