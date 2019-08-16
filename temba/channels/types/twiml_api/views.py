from uuid import uuid4

import phonenumbers
from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from ...models import Channel
from ...views import ALL_COUNTRIES, ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class TwimlApiClaimForm(ClaimViewMixin.Form):
        ROLES = (
            (Channel.ROLE_SEND + Channel.ROLE_RECEIVE, _("Messaging")),
            (Channel.ROLE_CALL + Channel.ROLE_ANSWER, _("Voice")),
            (Channel.ROLE_SEND + Channel.ROLE_RECEIVE + Channel.ROLE_CALL + Channel.ROLE_ANSWER, _("Both")),
        )
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES, label=_("Country"), help_text=_("The country this phone number is used in")
        )
        number = forms.CharField(
            max_length=14,
            min_length=1,
            label=_("Number"),
            help_text=_("The phone number without country code or short code you are connecting."),
        )
        url = forms.URLField(
            max_length=1024,
            label=_("TwiML REST API Host"),
            help_text=_("The publicly accessible URL for your TwiML REST API instance ex: https://api.twilio.com"),
        )
        role = forms.ChoiceField(
            choices=ROLES, label=_("Role"), help_text=_("Choose the role that this channel supports")
        )
        account_sid = forms.CharField(
            max_length=64,
            required=False,
            help_text=_("The Account SID to use to authenticate to the TwiML REST API"),
            widget=forms.TextInput(attrs={"autocomplete": "off"}),
        )
        account_token = forms.CharField(
            max_length=64,
            required=False,
            help_text=_("The Account Token to use to authenticate to the TwiML REST API"),
            widget=forms.TextInput(attrs={"autocomplete": "off"}),
        )
        max_concurrent_events = forms.IntegerField(
            min_value=1, required=False, help_text=_("Max active calls at the same time")
        )

    form_class = TwimlApiClaimForm

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()
        data = form.cleaned_data

        country = data.get("country")
        number = data.get("number")
        url = data.get("url")
        role = data.get("role")

        config = {
            Channel.CONFIG_SEND_URL: url,
            Channel.CONFIG_ACCOUNT_SID: data.get("account_sid", None),
            Channel.CONFIG_AUTH_TOKEN: data.get("account_token", str(uuid4())),
            Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain(),
            Channel.CONFIG_MAX_CONCURRENT_EVENTS: data.get("max_concurrent_events", None),
        }

        is_short_code = len(number) <= 6

        if not is_short_code:
            phone_number = phonenumbers.parse(number=number, region=country)
            address = f"+{str(phone_number.country_code)}{str(phone_number.national_number)}"

            name = phonenumbers.format_number(
                phonenumbers.parse(address, None), phonenumbers.PhoneNumberFormat.NATIONAL
            )
        else:
            role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE
            address = number
            name = number

        self.object = Channel.create(org, user, country, "TW", name=name, address=address, config=config, role=role)

        if not data.get("account_sid", None):
            config[Channel.CONFIG_ACCOUNT_SID] = f"{self.request.branding['name'].lower()}_{self.object.pk}"

            self.object.config = config
            self.object.save(update_fields=("config",))

        return super().form_valid(form)
