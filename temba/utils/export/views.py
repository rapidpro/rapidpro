from datetime import datetime, timedelta

from smartmin.views import SmartFormView

from django import forms
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import ContactField, ContactGroup
from temba.orgs.views import ModalMixin, OrgPermsMixin
from temba.utils.fields import SelectMultipleWidget, TembaDateField


class BaseExportView(ModalMixin, OrgPermsMixin, SmartFormView):
    """
    Base modal view for exports
    """

    class Form(forms.Form):
        MAX_FIELDS_COLS = 10
        MAX_GROUPS_COLS = 10

        start_date = TembaDateField(label=_("Start Date"))
        end_date = TembaDateField(label=_("End Date"))

        with_fields = forms.ModelMultipleChoiceField(
            ContactField.user_fields.none(),
            required=False,
            label=_("Fields"),
            widget=SelectMultipleWidget(attrs={"placeholder": _("Optional: Fields to include"), "searchable": True}),
        )
        with_groups = forms.ModelMultipleChoiceField(
            ContactGroup.objects.none(),
            required=False,
            label=_("Groups"),
            widget=SelectMultipleWidget(
                attrs={"placeholder": _("Optional: Group memberships to include"), "searchable": True}
            ),
        )

        def __init__(self, org, *args, **kwargs):
            super().__init__(*args, **kwargs)

            self.org = org
            self.fields["with_fields"].queryset = ContactField.get_fields(org).order_by(Lower("name"))
            self.fields["with_groups"].queryset = ContactGroup.get_groups(org=org, ready_only=True).order_by(
                Lower("name")
            )

        def clean_with_fields(self):
            data = self.cleaned_data["with_fields"]
            if data and len(data) > self.MAX_FIELDS_COLS:
                raise forms.ValidationError(_(f"You can only include up to {self.MAX_FIELDS_COLS} fields."))

            return data

        def clean_with_groups(self):
            data = self.cleaned_data["with_groups"]
            if data and len(data) > self.MAX_GROUPS_COLS:
                raise forms.ValidationError(_(f"You can only include up to {self.MAX_GROUPS_COLS} groups."))

            return data

        def clean(self):
            cleaned_data = super().clean()

            start_date = cleaned_data.get("start_date")
            end_date = cleaned_data.get("end_date")

            if start_date and start_date > timezone.now().astimezone(self.org.timezone).date():
                raise forms.ValidationError(_("Start date can't be in the future."))

            if end_date and start_date and end_date < start_date:
                raise forms.ValidationError(_("End date can't be before start date."))

            return cleaned_data

    form_class = Form
    submit_button_name = "Export"
    success_message = _("We are preparing your export and you will get a notification when it is ready.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["org"] = self.request.org
        return kwargs

    def derive_initial(self):
        initial = super().derive_initial()

        # default to last 90 days in org timezone
        tz = self.request.org.timezone
        end = datetime.now(tz)
        start = end - timedelta(days=90)

        initial["end_date"] = end.date()
        initial["start_date"] = start.date()
        return initial
