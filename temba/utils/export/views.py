from datetime import datetime, timedelta

from smartmin.views import SmartFormView

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.orgs.views import ModalMixin, OrgPermsMixin
from temba.utils.fields import TembaDateField


class BaseExportView(ModalMixin, OrgPermsMixin, SmartFormView):
    class Form(forms.Form):
        """
        Base form for exports
        """

        start_date = TembaDateField(label=_("Start Date"))
        end_date = TembaDateField(label=_("End Date"))

        def __init__(self, org, *args, **kwargs):
            super().__init__(*args, **kwargs)

            self.org = org

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
