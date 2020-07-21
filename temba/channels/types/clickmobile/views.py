import phonenumbers
from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import TEL_SCHEME

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        number = forms.CharField(
            max_length=14,
            min_length=1,
            label=_("Number"),
            help_text=_(
                "The Click Mobile phone number or short code you are connecting with country code. ex: +250788123124"
            ),
        )
        country = forms.ChoiceField(choices=(("GH", _("Ghana")), ("MW", _("Malawi"))), label=_("Country"),)
        username = forms.CharField(max_length=32, label=_("Username"), help_text=_("Your username on Click Mobile"))
        password = forms.CharField(max_length=64, label=_("Password"), help_text=_("Your password on Click Mobile"))
        app_id = forms.CharField(max_length=32, label=_("App ID"), help_text=_("Your app_id on Click Mobile"))
        org_id = forms.CharField(max_length=32, label=_("Org ID"), help_text=_("Your org_id on Click Mobile"))

        def clean_number(self):
            number = self.data["number"]

            # number is a shortcode, accept as is
            if len(number) > 0 and len(number) < 7:
                return number

            # otherwise, try to parse into an international format
            if number and number[0] != "+":
                number = "+" + number

            try:
                cleaned = phonenumbers.parse(number, None)
                return phonenumbers.format_number(cleaned, phonenumbers.PhoneNumberFormat.E164)
            except Exception:  # pragma: needs cover
                raise forms.ValidationError(
                    _("Invalid phone number, please include the country code. ex: +250788123123")
                )

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        data = form.cleaned_data
        config = {
            Channel.CONFIG_USERNAME: data["username"],
            Channel.CONFIG_PASSWORD: data["password"],
            "app_id": data["app_id"],
            "org_id": data["org_id"],
        }

        self.object = Channel.create(
            org=org,
            user=self.request.user,
            country=data["country"],
            channel_type="CM",
            name="Click Mobile: %s" % data["number"],
            address=data["number"],
            config=config,
            schemes=[TEL_SCHEME],
        )

        return super().form_valid(form)
