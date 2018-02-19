# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import pytz

from datetime import datetime, timedelta
from django import forms
from django.core.urlresolvers import reverse
from django.utils import timezone
from django.utils.timezone import get_current_timezone_name
from smartmin.views import SmartCRUDL, SmartUpdateView
from temba.orgs.views import OrgPermsMixin
from temba.utils import on_transaction_commit
from .models import Schedule


class BaseScheduleForm(object):

    def starts_never(self):
        return self.cleaned_data['start'] == "never"

    def starts_now(self):
        return self.cleaned_data['start'] == "now"

    def stopped(self):
        return self.cleaned_data['start'] == "stop"

    def is_recurring(self):
        return self.cleaned_data['repeat_period'] != 'O'

    def get_start_time(self):
        if self.cleaned_data['start'] == "later":
            start_datetime_value = self.cleaned_data['start_datetime_value']

            if start_datetime_value:
                return datetime.utcfromtimestamp(start_datetime_value).replace(tzinfo=pytz.utc)
            else:
                return None

        return timezone.now() - timedelta(days=1)  # pragma: needs cover


class ScheduleForm(BaseScheduleForm, forms.ModelForm):
    repeat_period = forms.ChoiceField(choices=Schedule.REPEAT_CHOICES)
    repeat_days = forms.IntegerField(required=False)
    start = forms.CharField(max_length=16)
    start_datetime_value = forms.IntegerField(required=False)

    def clean(self):
        data = super(ScheduleForm, self).clean()

        # only weekly gets repeat days
        if data['repeat_period'] != 'W':
            data['repeat_days'] = None

        return data

    class Meta:
        model = Schedule
        fields = '__all__'


class ScheduleCRUDL(SmartCRUDL):
    model = Schedule
    actions = ('update',)

    class Update(OrgPermsMixin, SmartUpdateView):
        form_class = ScheduleForm
        fields = ('repeat_period', 'repeat_days', 'start', 'start_datetime_value')
        field_config = dict(repeat_period=dict(label='Repeat', help=None))
        submit_button_name = 'Start'
        success_message = ''

        def get_success_url(self):
            broadcast = self.get_object().get_broadcast()
            trigger = self.get_object().get_trigger()

            if broadcast:
                return reverse('msgs.broadcast_schedule_list')
            elif trigger:  # pragma: needs cover
                return reverse('triggers.trigger_list')

            return reverse('public.public_welcome')

        def derive_success_message(self):
            return None

        def get_context_data(self, **kwargs):
            context = super(ScheduleCRUDL.Update, self).get_context_data(**kwargs)
            context['days'] = self.get_object().explode_bitmask()
            context['user_tz'] = get_current_timezone_name()
            context['user_tz_offset'] = int(timezone.localtime(timezone.now()).utcoffset().total_seconds() // 60)
            return context

        def save(self, *args, **kwargs):
            form = self.form
            schedule = self.object

            if form.starts_never():
                schedule.reset()

            elif form.stopped():
                schedule.reset()

            elif form.starts_now():
                schedule.next_fire = timezone.now() - timedelta(days=1)
                schedule.repeat_period = 'O'
                schedule.repeat_days = 0
                schedule.status = 'S'
                schedule.save()

            else:
                # Scheduled case
                schedule.status = 'S'
                schedule.repeat_period = form.cleaned_data['repeat_period']
                start_time = form.get_start_time()

                if start_time:
                    schedule.next_fire = start_time

                # create our recurrence
                if form.is_recurring():
                    if 'repeat_days' in form.cleaned_data:
                        days = form.cleaned_data['repeat_days']
                    schedule.repeat_days = days
                    schedule.repeat_hour_of_day = schedule.next_fire.hour
                    schedule.repeat_minute_of_hour = schedule.next_fire.minute
                    schedule.repeat_day_of_month = schedule.next_fire.day
                schedule.save()

            # trigger our schedule if necessary
            if schedule.is_expired():
                from .tasks import check_schedule_task
                on_transaction_commit(lambda: check_schedule_task.delay(schedule.pk))
