from datetime import datetime, timedelta

from smartmin.views import SmartFormView

from django import forms
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import ContactField
from temba.orgs.views import ModalMixin, OrgPermsMixin
from temba.utils.fields import SelectMultipleWidget, TembaDateField


class BaseExportView(ModalMixin, OrgPermsMixin, SmartFormView):
    """
    Base modal view for exports
    """

    class Form(forms.Form):
        MAX_FIELDS_COLS = 10

        start_date = TembaDateField(label=_("Start Date"))
        end_date = TembaDateField(label=_("End Date"))

        with_fields = forms.ModelMultipleChoiceField(
            ContactField.user_fields.none(),
            required=False,
            label=_("Fields"),
            widget=SelectMultipleWidget(attrs={"placeholder": _("Optional: Fields to include"), "searchable": True}),
        )

        def __init__(self, org, *args, **kwargs):
            super().__init__(*args, **kwargs)

            self.org = org
            self.fields["with_fields"].queryset = ContactField.user_fields.active_for_org(org=org).order_by(
                Lower("name")
            )

        def clean(self):
            cleaned_data = super().clean()

            start_date = cleaned_data.get("start_date")
            end_date = cleaned_data.get("end_date")
            with_fields = cleaned_data.get("with_fields")

            if start_date and start_date > timezone.now().astimezone(self.org.timezone).date():
                raise forms.ValidationError(_("Start date can't be in the future."))

            if end_date and start_date and end_date < start_date:
                raise forms.ValidationError(_("End date can't be before start date."))

            if with_fields and len(with_fields) > self.MAX_FIELDS_COLS:
                raise forms.ValidationError(
                    _(f"You can only include up to {self.MAX_FIELDS_COLS} fields in your export.")
                )

            return cleaned_data

    form_class = Form
    submit_button_name = "Export"

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
