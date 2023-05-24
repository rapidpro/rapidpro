import phonenumbers
import pycountry
import requests
from smartmin.views import SmartFormView

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.channels.views import BaseClaimNumberMixin, ChannelTypeMixin, ClaimViewMixin
from temba.orgs.views import OrgPermsMixin
from temba.utils import countries
from temba.utils.fields import SelectWidget
from temba.utils.http import http_headers
from temba.utils.models import generate_uuid

SUPPORTED_COUNTRIES = {
    "AU",  # Australia
    "BE",  # Belgium
    "CA",  # Canada
    "CZ",  # Czech Republic
    "EE",  # Estonia
    "FI",  # Finland
    "DE",  # Germany
    "HK",  # Hong Kong
    "HU",  # Hungary
    "IL",  # Israel
    "LT",  # Lithuania
    "MX",  # Mexico
    "NO",  # Norway
    "PK",  # Pakistan
    "PL",  # Poland
    "ZA",  # South Africa
    "SE",  # Sweden
    "CH",  # Switzerland
    "GB",  # United Kingdom
    "US",  # United States
}

COUNTRY_CHOICES = countries.choices(SUPPORTED_COUNTRIES)
CALLING_CODES = countries.calling_codes(SUPPORTED_COUNTRIES)


class ClaimView(BaseClaimNumberMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=COUNTRY_CHOICES, widget=SelectWidget(attrs={"searchable": True}))
        phone_number = forms.CharField(help_text=_("The phone number being added"))

        def clean_phone_number(self):
            if not self.cleaned_data.get("country", None):  # pragma: needs cover
                raise ValidationError(_("That number is not currently supported."))

            phone = self.cleaned_data["phone_number"]
            phone = phonenumbers.parse(phone, self.cleaned_data["country"])

            return phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)

    form_class = Form

    def pre_process(self, *args, **kwargs):
        auth_id = self.request.session.get(self.channel_type.CONFIG_AUTH_ID, None)
        auth_token = self.request.session.get(self.channel_type.CONFIG_AUTH_TOKEN, None)

        headers = http_headers(extra={"Content-Type": "application/json"})
        response = requests.get(
            "https://api.plivo.com/v1/Account/%s/" % auth_id, headers=headers, auth=(auth_id, auth_token)
        )

        if response.status_code == 200:
            return None
        else:
            return HttpResponseRedirect(reverse("channels.types.plivo.connect"))

    def is_valid_country(self, calling_code: int) -> bool:
        return calling_code in CALLING_CODES

    def is_messaging_country(self, country_code: str) -> bool:
        return country_code in SUPPORTED_COUNTRIES

    def get_search_url(self):
        return reverse("channels.types.plivo.search")

    def get_claim_url(self):
        return reverse("channels.types.plivo.claim")

    def get_supported_countries_tuple(self):
        return COUNTRY_CHOICES

    def get_search_countries_tuple(self):
        return COUNTRY_CHOICES

    def get_existing_numbers(self, org):
        auth_id = self.request.session.get(self.channel_type.CONFIG_AUTH_ID, None)
        auth_token = self.request.session.get(self.channel_type.CONFIG_AUTH_TOKEN, None)

        headers = http_headers(extra={"Content-Type": "application/json"})
        response = requests.get(
            "https://api.plivo.com/v1/Account/%s/Number/" % auth_id, headers=headers, auth=(auth_id, auth_token)
        )

        account_numbers = []
        if response.status_code == 200:
            data = response.json()
            for number_dict in data["objects"]:
                region = number_dict["region"]
                country_name = region.split(",")[-1].strip().title()
                country = pycountry.countries.get(name=country_name).alpha_2
                if len(number_dict["number"]) <= 6:
                    phone_number = number_dict["number"]
                else:
                    parsed = phonenumbers.parse("+" + number_dict["number"], None)
                    phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
                account_numbers.append(dict(number=phone_number, country=country))

        return account_numbers

    def claim_number(self, user, phone_number, country, role):
        auth_id = self.request.session.get(self.channel_type.CONFIG_AUTH_ID, None)
        auth_token = self.request.session.get(self.channel_type.CONFIG_AUTH_TOKEN, None)

        org = self.request.org

        plivo_uuid = generate_uuid()
        callback_domain = org.get_brand_domain()
        app_name = "%s_%s" % (callback_domain.lower().replace(".", "_"), plivo_uuid)

        message_url = f"https://{callback_domain}{reverse('courier.pl', args=[plivo_uuid, 'receive'])}"
        answer_url = f"{settings.STORAGE_URL}/plivo_voice_unavailable.xml"

        headers = http_headers(extra={"Content-Type": "application/json"})
        create_app_url = "https://api.plivo.com/v1/Account/%s/Application/" % auth_id

        response = requests.post(
            create_app_url,
            json=dict(app_name=app_name, answer_url=answer_url, message_url=message_url),
            headers=headers,
            auth=(auth_id, auth_token),
        )

        if response.status_code in [201, 200, 202]:
            plivo_app_id = response.json()["app_id"]
        else:  # pragma: no cover
            plivo_app_id = None

        plivo_config = {
            self.channel_type.CONFIG_AUTH_ID: auth_id,
            self.channel_type.CONFIG_AUTH_TOKEN: auth_token,
            self.channel_type.CONFIG_APP_ID: plivo_app_id,
            Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain(),
        }

        plivo_number = phone_number.strip("+ ").replace(" ", "")
        response = requests.get(
            "https://api.plivo.com/v1/Account/%s/Number/%s/" % (auth_id, plivo_number),
            headers=headers,
            auth=(auth_id, auth_token),
        )

        if response.status_code != 200:
            response = requests.post(
                "https://api.plivo.com/v1/Account/%s/PhoneNumber/%s/" % (auth_id, plivo_number),
                headers=headers,
                auth=(auth_id, auth_token),
            )

            if response.status_code != 201:  # pragma: no cover
                raise Exception(
                    _("There was a problem claiming that number, please check the balance on your account.")
                )

            response = requests.get(
                "https://api.plivo.com/v1/Account/%s/Number/%s/" % (auth_id, plivo_number),
                headers=headers,
                auth=(auth_id, auth_token),
            )

        if response.status_code == 200:
            response = requests.post(
                "https://api.plivo.com/v1/Account/%s/Number/%s/" % (auth_id, plivo_number),
                json=dict(app_id=plivo_app_id),
                headers=headers,
                auth=(auth_id, auth_token),
            )

            if response.status_code != 202:  # pragma: no cover
                raise Exception(_("There was a problem updating that number, please try again."))

        phone_number = "+" + plivo_number
        phone = phonenumbers.format_number(
            phonenumbers.parse(phone_number, None), phonenumbers.PhoneNumberFormat.NATIONAL
        )

        channel = Channel.create(
            org, user, country, "PL", name=phone, address=phone_number, config=plivo_config, uuid=plivo_uuid
        )

        return channel

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_creds_account"] = self.request.session.get(self.channel_type.CONFIG_AUTH_ID, None)

        return context

    def remove_api_credentials_from_session(self):
        if self.channel_type.CONFIG_AUTH_ID in self.request.session:
            del self.request.session[self.channel_type.CONFIG_AUTH_ID]
        if self.channel_type.CONFIG_AUTH_TOKEN in self.request.session:
            del self.request.session[self.channel_type.CONFIG_AUTH_TOKEN]


class SearchView(ChannelTypeMixin, OrgPermsMixin, SmartFormView):
    class Form(forms.Form):
        country = forms.ChoiceField(choices=COUNTRY_CHOICES)
        pattern = forms.CharField(max_length=7, required=False)

    form_class = Form
    permission = "channels.channel_claim"

    def form_valid(self, form, *args, **kwargs):
        data = form.cleaned_data
        auth_id = self.request.session.get(self.channel_type.CONFIG_AUTH_ID, None)
        auth_token = self.request.session.get(self.channel_type.CONFIG_AUTH_TOKEN, None)

        try:
            params = dict(country_iso=data["country"], pattern=data.get("pattern"))
            url = f"https://api.plivo.com/v1/Account/{auth_id}/PhoneNumber/?{urlencode(params)}"

            headers = http_headers(extra={"Content-Type": "application/json"})
            response = requests.get(url, headers=headers, auth=(auth_id, auth_token))

            if response.status_code == 200:
                response_data = response.json()
                results_numbers = ["+" + number_dict["number"] for number_dict in response_data["objects"]]
            else:
                return JsonResponse({"error": response.text})

            numbers = []
            for number in results_numbers:
                numbers.append(
                    phonenumbers.format_number(
                        phonenumbers.parse(number, None), phonenumbers.PhoneNumberFormat.INTERNATIONAL
                    )
                )

            return JsonResponse(numbers, safe=False)
        except Exception as e:
            return JsonResponse({"error": str(e)})


class Connect(ChannelTypeMixin, OrgPermsMixin, SmartFormView):
    class PlivoConnectForm(forms.Form):
        auth_id = forms.CharField(help_text=_("Your Plivo auth ID"))
        auth_token = forms.CharField(help_text=_("Your Plivo auth token"))

        def clean(self):
            super().clean()

            auth_id = self.cleaned_data.get("auth_id", None)
            auth_token = self.cleaned_data.get("auth_token", None)

            headers = http_headers(extra={"Content-Type": "application/json"})

            response = requests.get(
                "https://api.plivo.com/v1/Account/%s/" % auth_id, headers=headers, auth=(auth_id, auth_token)
            )

            if response.status_code != 200:
                raise ValidationError(
                    _("Your Plivo auth ID and auth token seem invalid. Please check them again and retry.")
                )

            return self.cleaned_data

    form_class = PlivoConnectForm
    permission = "channels.channel_claim"
    submit_button_name = "Save"
    template_name = "channels/types/plivo/connect.html"
    field_config = dict(auth_id=dict(label=""), auth_token=dict(label=""))
    success_message = "Plivo credentials verified. You can now add a Plivo channel."
    menu_path = "/settings/workspace"

    def get_success_url(self):
        return reverse("channels.types.plivo.claim")

    def pre_process(self, *args, **kwargs):
        reset_creds = self.request.GET.get("reset_creds", "")

        org = self.request.org
        last_plivo_channel = (
            org.channels.filter(is_active=True, channel_type=self.channel_type.code).order_by("-created_on").first()
        )

        if last_plivo_channel and not reset_creds:
            self.request.session[self.channel_type.CONFIG_AUTH_ID] = last_plivo_channel.config.get(
                self.channel_type.CONFIG_AUTH_ID, ""
            )
            self.request.session[self.channel_type.CONFIG_AUTH_TOKEN] = last_plivo_channel.config.get(
                self.channel_type.CONFIG_AUTH_TOKEN, ""
            )
            return HttpResponseRedirect(self.get_success_url())

        return None

    def form_valid(self, form):
        auth_id = form.cleaned_data["auth_id"]
        auth_token = form.cleaned_data["auth_token"]

        # add the credentials to the session
        self.request.session[self.channel_type.CONFIG_AUTH_ID] = auth_id
        self.request.session[self.channel_type.CONFIG_AUTH_TOKEN] = auth_token

        return HttpResponseRedirect(self.get_success_url())
