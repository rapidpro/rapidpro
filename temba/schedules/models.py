import calendar
import logging
from datetime import time, timedelta

from dateutil.relativedelta import relativedelta
from smartmin.models import SmartModel

from django.contrib.humanize.templatetags.humanize import ordinal
from django.db import models
from django.db.models import Index, Q
from django.utils import timezone
from django.utils.timesince import timeuntil
from django.utils.translation import gettext_lazy as _

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

    REPEAT_DAYS_CHOICES = (
        ("M", _("Monday")),
        ("T", _("Tuesday")),
        ("W", _("Wednesday")),
        ("R", _("Thursday")),
        ("F", _("Friday")),
        ("S", _("Saturday")),
        ("U", _("Sunday")),
    )

    DAYS_OF_WEEK_DISPLAY = {
        "M": _("Monday"),
        "T": _("Tuesday"),
        "W": _("Wednesday"),
        "R": _("Thursday"),
        "F": _("Friday"),
        "S": _("Saturday"),
        "U": _("Sunday"),
    }

    # ordered in the same way as python's weekday function
    DAYS_OF_WEEK_OFFSET = "MTWRFSU"

    org = models.ForeignKey("orgs.Org", on_delete=models.PROTECT, related_name="schedules")
    repeat_period = models.CharField(max_length=1, choices=REPEAT_CHOICES)

    # the time of the day this schedule will fire (in org timezone)
    repeat_hour_of_day = models.IntegerField(null=True)
    repeat_minute_of_hour = models.IntegerField(null=True)

    # the day of the month this will repeat on (only for monthly repeats, 1-31)
    repeat_day_of_month = models.IntegerField(null=True)

    # what days of the week this will repeat on (only for weekly repeats) One of MTWRFSU
    repeat_days_of_week = models.CharField(null=True, max_length=7)

    next_fire = models.DateTimeField(null=True)
    last_fire = models.DateTimeField(null=True)

    @classmethod
    def create_schedule(cls, org, user, start_time, repeat_period, repeat_days_of_week=None, now=None):
        assert not repeat_days_of_week or set(repeat_days_of_week).issubset(cls.DAYS_OF_WEEK_OFFSET)

        schedule = cls(repeat_period=repeat_period, created_by=user, modified_by=user, org=org)
        schedule.update_schedule(user, start_time, repeat_period, repeat_days_of_week, now=now)
        return schedule

    def update_schedule(self, user, start_time, repeat_period, repeat_days_of_week, now=None):
        if not now:
            now = timezone.now()

        tz = self.org.timezone

        # no start time means we aren't repeating anymore
        if not start_time:
            repeat_period = Schedule.REPEAT_NEVER

        self.repeat_period = repeat_period

        if repeat_period == Schedule.REPEAT_NEVER:
            self.repeat_minute_of_hour = None
            self.repeat_hour_of_day = None
            self.repeat_day_of_month = None
            self.repeat_days_of_week = None

            self.next_fire = start_time if start_time and start_time > now else None
            self.save()

        else:
            # our start time needs to be in the org timezone so that we always fire at the
            # appropriate hour regardless of timezone / dst changes
            start_time = tz.normalize(start_time.astimezone(tz))

            self.repeat_hour_of_day = start_time.hour
            self.repeat_minute_of_hour = start_time.minute
            self.repeat_days_of_week = None
            self.repeat_day_of_month = None

            if repeat_period == Schedule.REPEAT_WEEKLY:
                self.repeat_days_of_week = repeat_days_of_week

            elif repeat_period == Schedule.REPEAT_MONTHLY:
                self.repeat_day_of_month = start_time.day

            # for recurring schedules if the start time is in the past, calculate our next fire and set that
            if start_time < now:
                self.next_fire = self.calculate_next_fire(now)
            else:
                self.next_fire = start_time

            self.modified_by = user
            self.modified_on = timezone.now()
            self.save()

    def get_broadcast(self):
        if hasattr(self, "broadcast"):
            return self.broadcast

    def calculate_next_fire(self, now):
        """
        Get the next point in the future when our schedule should fire again. Note this should only be called to find
        the next scheduled event as it will force the next date to meet the criteria in day_of_month, days_of_week etc..
        """
        if self.repeat_period == Schedule.REPEAT_NEVER:
            return None

        tz = self.org.timezone
        hour = self.repeat_hour_of_day
        minute = self.repeat_minute_of_hour

        # start from the trigger date
        next_fire = now.astimezone(tz)
        next_fire = tz.normalize(next_fire.replace(hour=hour, minute=minute, second=0, microsecond=0))

        # if monthly, set to the day of the month scheduled and move forward until we are in the future
        if self.repeat_period == Schedule.REPEAT_MONTHLY:
            while True:
                (weekday, days) = calendar.monthrange(next_fire.year, next_fire.month)
                day_of_month = min(days, self.repeat_day_of_month)
                next_fire = tz.normalize(next_fire.replace(day=day_of_month, hour=hour, minute=minute))
                if next_fire > now:
                    break

                next_fire = tz.normalize(next_fire + relativedelta(months=1))

            return next_fire

        # if weekly, move forward until we're in the future and on an appropriate day of the week
        elif self.repeat_period == Schedule.REPEAT_WEEKLY:
            assert self.repeat_days_of_week != "" and self.repeat_days_of_week is not None

            while next_fire <= now or self._day_of_week(next_fire) not in self.repeat_days_of_week:
                next_fire = tz.normalize(tz.normalize(next_fire + timedelta(days=1)).replace(hour=hour, minute=minute))

            return next_fire

        elif self.repeat_period == Schedule.REPEAT_DAILY:
            while next_fire <= now:
                next_fire = tz.normalize(tz.normalize(next_fire + timedelta(days=1)).replace(hour=hour, minute=minute))

            return next_fire

    def get_repeat_days_display(self):
        return [Schedule.DAYS_OF_WEEK_DISPLAY[d] for d in self.repeat_days_of_week] if self.repeat_days_of_week else []

    def get_display(self):
        if self.repeat_period == self.REPEAT_NEVER:
            return _("in %(timeperiod)s") % {"timeperiod": timeuntil(self.next_fire)} if self.next_fire else ""
        elif self.repeat_period == self.REPEAT_DAILY:
            time_of_day = time(self.repeat_hour_of_day, self.repeat_minute_of_hour, 0).strftime("%H:%M")
            return _("each day at %(time)s") % {"time": time_of_day}
        elif self.repeat_period == self.REPEAT_WEEKLY:
            days = [str(day) for day in self.get_repeat_days_display()]
            return _("each week on %(daysofweek)s" % {"daysofweek": ", ".join(days)})
        elif self.repeat_period == self.REPEAT_MONTHLY:
            return _("each month on the %(dayofmonth)s" % {"dayofmonth": ordinal(self.repeat_day_of_month)})

    @staticmethod
    def _day_of_week(d):
        """
        Converts a datetime to a day of the week code (M..U)
        """
        return Schedule.DAYS_OF_WEEK_OFFSET[d.weekday()]

    def release(self, user):
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=("is_active", "modified_by", "modified_on"))

    def __repr__(self):  # pragma: no cover
        return f'<Schedule: id={self.id} repeat="{self.get_display()}"  next={str(self.next_fire)}>'

    class Meta:
        indexes = [
            # used by mailroom for fetching schedules that need to be fired
            Index(
                name="schedules_next_fire_active",
                fields=["next_fire"],
                condition=Q(is_active=True, next_fire__isnull=False),
            )
        ]
