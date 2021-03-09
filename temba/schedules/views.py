from smartmin.views import SmartCRUDL, SmartUpdateView

from django import forms
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.orgs.views import OrgObjPermsMixin
from temba.utils.fields import InputWidget, SelectMultipleWidget
from temba.utils.views import ComponentFormMixin

from .models import Schedule


class BaseScheduleForm(object):
    def clean_repeat_days_of_week(self):  # pragma: needs cover
        data = self.cleaned_data["repeat_days_of_week"]

        # validate days of the week for weekly schedules
        if data:
            for c in data:
                if c not in Schedule.DAYS_OF_WEEK_OFFSET:
                    raise forms.ValidationError(_("%(day)s is not a valid day of the week"), params={"day": c})

        return data

    def clean(self):
        data = self.cleaned_data
        if data["repeat_period"] == Schedule.REPEAT_WEEKLY and not data.get("repeat_days_of_week"):
            raise forms.ValidationError(_("Must specify at least one day of the week"))

        return data


class ScheduleForm(BaseScheduleForm, forms.ModelForm):
    repeat_period = forms.ChoiceField(choices=Schedule.REPEAT_CHOICES)

    repeat_days_of_week = forms.MultipleChoiceField(
        choices=Schedule.REPEAT_DAYS_CHOICES,
        label="Repeat Days",
        required=False,
        widget=SelectMultipleWidget(attrs=({"placeholder": _("Select days to repeat on")})),
    )

    start_datetime = forms.DateTimeField(
        required=False,
        label=_(" "),
        widget=InputWidget(attrs={"datetimepicker": True, "placeholder": "Select a time to send the message"}),
    )

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["start_datetime"].help_text = _("%s Time Zone" % org.timezone)

    def clean_repeat_days_of_week(self):
        return "".join(self.cleaned_data["repeat_days_of_week"])

    class Meta:
        model = Schedule
        fields = "__all__"


class ScheduleCRUDL(SmartCRUDL):
    model = Schedule
    actions = ("update",)

    class Update(OrgObjPermsMixin, ComponentFormMixin, SmartUpdateView):
        form_class = ScheduleForm
        fields = ("repeat_period", "repeat_days_of_week", "start_datetime")
        field_config = dict(repeat_period=dict(label="Repeat", help=None))
        submit_button_name = "Start"
        success_message = ""

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()
            initial["start_datetime"] = self.get_object().next_fire
            return initial

        def get_success_url(self):
            broadcast = self.get_object().get_broadcast()
            assert broadcast is not None
            return reverse("msgs.broadcast_schedule_read", args=[broadcast.pk])

        def derive_success_message(self):
            return None

        def save(self, *args, **kwargs):
            form = self.form
            schedule = self.object

            schedule.update_schedule(
                form.cleaned_data.get("start_datetime"),
                form.cleaned_data.get("repeat_period"),
                form.cleaned_data.get("repeat_days_of_week"),
            )
