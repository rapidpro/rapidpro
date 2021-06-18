from smartmin.views import SmartCRUDL, SmartUpdateView

from django import forms
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.orgs.views import OrgObjPermsMixin
from temba.utils.fields import InputWidget, SelectMultipleWidget, SelectWidget
from temba.utils.views import ComponentFormMixin

from .models import Schedule


class ScheduleFormMixin(forms.Form):
    start_datetime = forms.DateTimeField(
        label=_("Start Time"),
        widget=InputWidget(attrs={"datetimepicker": True, "placeholder": _("Select a date and time")}),
    )
    repeat_period = forms.ChoiceField(choices=Schedule.REPEAT_CHOICES, label=_("Repeat"), widget=SelectWidget())
    repeat_days_of_week = forms.MultipleChoiceField(
        choices=Schedule.REPEAT_DAYS_CHOICES,
        label=_("Repeat Days"),
        required=False,
        widget=SelectMultipleWidget(attrs=({"placeholder": _("Select days to repeat on")})),
    )

    def set_user(self, user):
        """
        Because this mixin is mixed with other forms it can't have a __init__ constructor that takes non standard Django
        forms args and kwargs, so we have to customize based on user after the form has been created.
        """
        tz = user.get_org().timezone
        self.fields["start_datetime"].help_text = _("First time this should happen in the %s timezone.") % tz

    def clean_repeat_days_of_week(self):
        return "".join(self.cleaned_data["repeat_days_of_week"])

    def clean(self):
        cleaned_data = super().clean()

        if cleaned_data["repeat_period"] == Schedule.REPEAT_WEEKLY and not cleaned_data.get("repeat_days_of_week"):
            raise forms.ValidationError(_("Must specify at least one day of the week."))

        return cleaned_data

    class Meta:
        fields = ("start_datetime", "repeat_period", "repeat_days_of_week")


class ScheduleCRUDL(SmartCRUDL):
    model = Schedule
    actions = ("update",)

    class Update(OrgObjPermsMixin, ComponentFormMixin, SmartUpdateView):
        class Form(forms.ModelForm, ScheduleFormMixin):
            def __init__(self, user, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.set_user(user)

            def clean(self):
                super().clean()

                ScheduleFormMixin.clean(self)

            class Meta:
                model = Schedule
                fields = ScheduleFormMixin.Meta.fields

        form_class = Form
        submit_button_name = "Start"
        success_message = ""

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()
            initial["start_datetime"] = self.get_object().next_fire
            return initial

        def get_success_url(self):
            broadcast = self.get_object().get_broadcast()
            assert broadcast is not None
            return reverse("msgs.broadcast_schedule_read", args=[broadcast.id])

        def save(self, *args, **kwargs):
            form = self.form
            schedule = self.object

            schedule.update_schedule(
                form.cleaned_data.get("start_datetime"),
                form.cleaned_data.get("repeat_period"),
                form.cleaned_data.get("repeat_days_of_week"),
            )
