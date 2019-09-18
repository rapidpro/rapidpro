from datetime import datetime, timedelta

import pytz
from smartmin.views import SmartCRUDL, SmartUpdateView

from django import forms
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import get_current_timezone_name

from temba.orgs.views import OrgPermsMixin
from temba.utils import on_transaction_commit

from .models import Schedule


class BaseScheduleForm(object):
    def is_recurring(self):
        return self.cleaned_data["repeat_period"] != Schedule.REPEAT_NEVER

    def get_start_time(self, tz):
        if self.cleaned_data["start"] == "later":
            start_datetime_value = self.cleaned_data["start_datetime_value"]

            if start_datetime_value:
                start_datetime = tz.normalize(datetime.utcfromtimestamp(start_datetime_value).astimezone(tz))
                return start_datetime
            else:
                return None

        return None


class ScheduleForm(BaseScheduleForm, forms.ModelForm):
    start = forms.ChoiceField(choices=(("stop", "Stop Schedule"), ("later", "Schedule for later")))
    repeat_period = forms.ChoiceField(choices=Schedule.REPEAT_CHOICES)
    repeat_days_of_week = forms.CharField(required=False)
    start = forms.CharField(max_length=16)
    start_datetime_value = forms.IntegerField(required=False)

    class Meta:
        model = Schedule
        fields = "__all__"


class ScheduleCRUDL(SmartCRUDL):
    model = Schedule
    actions = ("update",)

    class Update(OrgPermsMixin, SmartUpdateView):
        form_class = ScheduleForm
        fields = ("repeat_period", "repeat_days_of_week", "start", "start_datetime_value")
        field_config = dict(repeat_period=dict(label="Repeat", help=None))
        submit_button_name = "Start"
        success_message = ""

        def get_success_url(self):
            broadcast = self.get_object().get_broadcast()
            trigger = self.get_object().get_trigger()

            if broadcast:
                return reverse("msgs.broadcast_schedule_list")
            elif trigger:  # pragma: needs cover
                return reverse("triggers.trigger_list")

            return reverse("public.public_welcome")

        def derive_success_message(self):
            return None

        def get_context_data(self, **kwargs):
            org = self.get_object().org
            context = super().get_context_data(**kwargs)
            context["days"] = self.get_object().repeat_days_of_week
            context["user_tz"] = get_current_timezone_name()
            context["user_tz_offset"] = int(timezone.now().astimezone(org.timezone).utcoffset().total_seconds() // 60)
            return context

        def save(self, *args, **kwargs):
            form = self.form

            schedule = self.object
            schedule.org = self.derive_org()

            start_time = form.get_start_time(schedule.org.timezone)
            print(start_time)

            schedule.update_schedule(
                start_time, form.cleaned_data.get("repeat_period"), form.cleaned_data.get("repeat_days_of_week")
            )
