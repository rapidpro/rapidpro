import phonenumbers
from smartmin.views import SmartFormView

from django import forms
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.orgs.views import OrgPermsMixin
from temba.utils import countries
from temba.utils.fields import InputWidget, SelectWidget
from temba.utils.models import generate_uuid

from ...models import Channel
from ...views import BaseClaimNumberMixin, ChannelTypeMixin, ClaimViewMixin, UpdateTelChannelForm
from .client import VonageClient

SUPPORTED_COUNTRIES = {
    "AC",  # Ascension Island
    "AD",  # Andorra
    "AE",  # United Arab Emirates
    "AF",  # Afghanistan
    "AG",  # Antigua and Barbuda
    "AI",  # Anguilla
    "AL",  # Albania
    "AM",  # Armenia
    "AO",  # Angola
    "AR",  # Argentina
    "AS",  # American Samoa
    "AT",  # Austria
    "AU",  # Australia
    "AW",  # Aruba
    "AZ",  # Azerbaijan
    "BA",  # Bosnia and Herzegovina
    "BB",  # Barbados
    "BD",  # Bangladesh
    "BE",  # Belgium
    "BF",  # Burkina Faso
    "BG",  # Bulgaria
    "BH",  # Bahrain
    "BI",  # Burundi
    "BJ",  # Benin
    "BM",  # Bermuda
    "BN",  # Brunei
    "BO",  # Bolivia
    "BQ",  # Bonaire, Sint Eustatius and Saba
    "BR",  # Brazil
    "BS",  # Bahamas
    "BT",  # Bhutan
    "BW",  # Botswana
    "BY",  # Belarus
    "BZ",  # Belize
    "CA",  # Canada
    "CD",  # Democratic Republic of the Congo
    "CF",  # Central African Republic
    "CG",  # Republic Of The Congo
    "CH",  # Switzerland
    "CI",  # Ivory Coast
    "CK",  # Cook Islands
    "CL",  # Chile
    "CM",  # Cameroon
    "CN",  # China
    "CO",  # Colombia
    "CR",  # Costa Rica
    "CU",  # Cuba
    "CV",  # Cape Verde
    "CW",  # Curacao
    "CY",  # Cyprus
    "CZ",  # Czechia
    "DE",  # Germany
    "DJ",  # Djibouti
    "DK",  # Denmark
    "DM",  # Dominica
    "DO",  # Dominican Republic
    "DZ",  # Algeria
    "EC",  # Ecuador
    "EE",  # Estonia
    "EG",  # Egypt
    "ER",  # Eritrea
    "ES",  # Spain
    "ET",  # Ethiopia
    "FI",  # Finland
    "FJ",  # Fiji
    "FM",  # Micronesia
    "FO",  # Faroe Islands
    "FR",  # France
    "GA",  # Gabon
    "GB",  # United Kingdom
    "GD",  # Grenada
    "GE",  # Georgia
    "GF",  # French Guiana
    "GH",  # Ghana
    "GI",  # Gibraltar
    "GL",  # Greenland
    "GM",  # Gambia
    "GN",  # Guinea
    "GP",  # Guadeloupe
    "GQ",  # Equatorial Guinea
    "GR",  # Greece
    "GT",  # Guatemala
    "GU",  # Guam
    "GW",  # Guinea-Bissau
    "GY",  # Guyana
    "HK",  # Hong Kong
    "HN",  # Honduras
    "HR",  # Croatia
    "HT",  # Haiti
    "HU",  # Hungary
    "ID",  # Indonesia
    "IE",  # Ireland
    "IL",  # Israel
    "IN",  # India
    "IQ",  # Iraq
    "IR",  # Iran
    "IS",  # Iceland
    "IT",  # Italy
    "JM",  # Jamaica
    "JO",  # Jordan
    "JP",  # Japan
    "KE",  # Kenya
    "KG",  # Kyrgyzstan
    "KH",  # Cambodia
    "KI",  # Kiribati
    "KM",  # Comoros
    "KN",  # Saint Kitts and Nevis
    "KR",  # South Korea
    "KW",  # Kuwait
    "KY",  # Cayman Islands
    "KZ",  # Kazakhstan
    "LA",  # Laos
    "LB",  # Lebanon
    "LC",  # Saint Lucia
    "LI",  # Liechtenstein
    "LK",  # Sri Lanka
    "LR",  # Liberia
    "LS",  # Lesotho
    "LT",  # Lithuania
    "LU",  # Luxembourg
    "LV",  # Latvia
    "LY",  # Libya
    "MA",  # Morocco
    "MC",  # Monaco
    "MD",  # Moldova
    "ME",  # Montenegro
    "MG",  # Madagascar
    "MH",  # Marshall Islands
    "MK",  # Macedonia
    "ML",  # Mali
    "MM",  # Myanmar
    "MN",  # Mongolia
    "MO",  # Macau
    "MP",  # Northern Mariana Islands
    "MQ",  # Martinique
    "MR",  # Mauritania
    "MS",  # Montserrat
    "MT",  # Malta
    "MU",  # Mauritius
    "MV",  # Maldives
    "MW",  # Malawi
    "MX",  # Mexico
    "MY",  # Malaysia
    "MZ",  # Mozambique
    "NA",  # Namibia
    "NC",  # New Caledonia
    "NE",  # Niger
    "NG",  # Nigeria
    "NI",  # Nicaragua
    "NL",  # Netherlands
    "NO",  # Norway
    "NP",  # Nepal
    "NR",  # Nauru
    "NZ",  # New Zealand
    "OM",  # Oman
    "PA",  # Panama
    "PE",  # Peru
    "PF",  # French Polynesia
    "PG",  # Papua New Guinea
    "PH",  # Philippines
    "PK",  # Pakistan
    "PL",  # Poland
    "PM",  # Saint Pierre and Miquelon
    "PR",  # Puerto Rico
    "PS",  # Palestinian Territory
    "PT",  # Portugal
    "PW",  # Palau
    "PY",  # Paraguay
    "QA",  # Qatar
    "RE",  # Réunion Island
    "RO",  # Romania
    "RS",  # Serbia
    "RU",  # Russia
    "RW",  # Rwanda
    "SA",  # Saudi Arabia
    "SB",  # Solomon Islands
    "SC",  # Seychelles
    "SD",  # Sudan
    "SE",  # Sweden
    "SG",  # Singapore
    "SI",  # Slovenia
    "SK",  # Slovakia
    "SL",  # Sierra Leone
    "SM",  # San Marino
    "SN",  # Senegal
    "SO",  # Somalia
    "SR",  # Suriname
    "SS",  # South Sudan
    "ST",  # Sao Tome and Principe
    "SV",  # El Salvador
    "SX",  # Sint Maarten (Dutch Part)
    "SY",  # Syria
    "SZ",  # Swaziland
    "TC",  # Turks and Caicos Islands
    "TD",  # Chad
    "TG",  # Togo
    "TH",  # Thailand
    "TJ",  # Tajikistan
    "TL",  # East Timor
    "TM",  # Turkmenistan
    "TN",  # Tunisia
    "TO",  # Tonga
    "TR",  # Turkey
    "TT",  # Trinidad and Tobago
    "TW",  # Taiwan
    "TZ",  # Tanzania
    "UA",  # Ukraine
    "UG",  # Uganda
    "US",  # United States
    "UY",  # Uruguay
    "UZ",  # Uzbekistan
    "VC",  # Saint Vincent and The Grenadines
    "VE",  # Venezuela
    "VG",  # Virgin Islands, British
    "VI",  # Virgin Islands, US
    "VN",  # Vietnam
    "VU",  # Vanuatu
    "WS",  # Samoa
    "XK",  # Kosovo
    "YE",  # Yemen
    "YT",  # Mayotte
    "ZA",  # South Africa
    "ZM",  # Zambia
    "ZW",  # Zimbabwe
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

        def clean(self):
            self.cleaned_data["address"] = self.cleaned_data["phone_number"]
            return super().clean()

    form_class = Form

    def pre_process(self, *args, **kwargs):
        client = self.get_vonage_client()

        if client:
            return None
        else:  # pragma: needs cover
            return HttpResponseRedirect(reverse("channels.types.vonage.connect"))

    def get_vonage_client(self):
        api_key = self.request.session.get(self.channel_type.SESSION_API_KEY, None)
        api_secret = self.request.session.get(self.channel_type.SESSION_API_SECRET, None)

        if api_key and api_secret:
            return VonageClient(api_key, api_secret)
        return None

    def is_valid_country(self, calling_code: int) -> bool:
        return calling_code in CALLING_CODES

    def is_messaging_country(self, country_code: str) -> bool:
        return country_code in SUPPORTED_COUNTRIES

    def get_search_url(self):
        return reverse("channels.types.vonage.search")

    def get_claim_url(self):
        return reverse("channels.types.vonage.claim")

    def get_supported_countries_tuple(self):
        return COUNTRY_CHOICES

    def get_search_countries_tuple(self):
        return COUNTRY_CHOICES

    def get_existing_numbers(self, org):
        client = self.get_vonage_client()
        if client:
            account_numbers = client.get_numbers(size=100)

        numbers = []
        for number in account_numbers:
            if number["type"] == "mobile-shortcode":  # pragma: needs cover
                phone_number = number["msisdn"]
            else:
                parsed = phonenumbers.parse(number["msisdn"], number["country"])
                phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
            numbers.append(dict(number=phone_number, country=number["country"]))

        return numbers

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_creds_account"] = self.request.session.get(self.channel_type.SESSION_API_KEY, None)

        return context

    def claim_number(self, user, phone_number, country, role):
        org = self.request.org
        client = self.get_vonage_client()

        matching_phones = client.get_numbers(phone_number)
        is_shortcode = False

        # try it with just the national code (for short codes)
        if not matching_phones:
            parsed = phonenumbers.parse(phone_number, None)
            shortcode = str(parsed.national_number)

            matching_phones = client.get_numbers(shortcode)

            if matching_phones:
                is_shortcode = True
                phone_number = shortcode

        # buy the number if we have to
        if not matching_phones:
            try:
                client.buy_number(country, phone_number)
                matching_phones = client.get_numbers(phone_number)
            except Exception as e:
                raise Exception(
                    _(
                        "There was a problem claiming that number, please check the balance on your account. "
                        "Note that you can only claim numbers after adding credit to your Vonage account."
                    )
                    + "\n"
                    + str(e)
                )

        # what does this number support?
        features = [elt.upper() for elt in matching_phones[0]["features"]]
        supports_msgs = "SMS" in features
        supports_voice = "VOICE" in features
        role = ""
        if supports_msgs:
            role += Channel.ROLE_SEND + Channel.ROLE_RECEIVE

        if supports_voice:
            role += Channel.ROLE_ANSWER + Channel.ROLE_CALL

        channel_uuid = generate_uuid()
        callback_domain = org.get_brand_domain()
        receive_url = "https://" + callback_domain + reverse("courier.nx", args=[channel_uuid, "receive"])

        # if it supports voice, create new voice app for this number
        if supports_voice:
            app_id, app_private_key = client.create_application(org.get_brand_domain(), channel_uuid)
        else:
            app_id = None
            app_private_key = None

        # update the delivery URLs for it
        try:
            client.update_number(country, phone_number, receive_url, app_id)

        except Exception as e:  # pragma: no cover
            # shortcodes don't seem to claim correctly, move forward anyways
            if not is_shortcode:
                raise Exception(
                    _("There was a problem claiming that number, please check the balance on your account.")
                    + "\n"
                    + str(e)
                )

        if is_shortcode:
            phone = phone_number
        else:
            parsed = phonenumbers.parse(phone_number, None)
            phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)

        config = {
            self.channel_type.CONFIG_APP_ID: app_id,
            self.channel_type.CONFIG_APP_PRIVATE_KEY: app_private_key,
            self.channel_type.CONFIG_API_KEY: self.request.session.get(self.channel_type.SESSION_API_KEY),
            self.channel_type.CONFIG_API_SECRET: self.request.session.get(self.channel_type.SESSION_API_SECRET),
            Channel.CONFIG_CALLBACK_DOMAIN: callback_domain,
        }

        return Channel.create(
            org,
            user,
            country,
            self.channel_type,
            name=phone,
            address=phone_number,
            role=role,
            config=config,
            uuid=channel_uuid,
            tps=1,
        )


class SearchView(ChannelTypeMixin, OrgPermsMixin, SmartFormView):
    """
    Endpoint for searching for numbers to claim
    """

    class Form(forms.Form):
        country = forms.ChoiceField(choices=COUNTRY_CHOICES)
        pattern = forms.CharField(max_length=7, required=False)

    form_class = Form
    permission = "channels.channel_claim"

    def get_vonage_client(self):
        api_key = self.request.session.get(self.channel_type.SESSION_API_KEY, None)
        api_secret = self.request.session.get(self.channel_type.SESSION_API_SECRET, None)

        if api_key and api_secret:
            return VonageClient(api_key, api_secret)
        return None  # pragma: no cover

    def form_valid(self, form, *args, **kwargs):
        client = self.get_vonage_client()
        data = form.cleaned_data

        available_numbers = client.search_numbers(data["country"], data.get("pattern"))
        numbers = []

        for number in available_numbers:
            numbers.append(
                phonenumbers.format_number(
                    phonenumbers.parse(number["msisdn"], data["country"]),
                    phonenumbers.PhoneNumberFormat.INTERNATIONAL,
                )
            )

        return JsonResponse(numbers, safe=False)


class UpdateForm(UpdateTelChannelForm):
    class Meta(UpdateTelChannelForm.Meta):
        readonly = ()


class Connect(ChannelTypeMixin, OrgPermsMixin, SmartFormView):
    class VonageConnectForm(forms.Form):
        api_key = forms.CharField(help_text=_("Your Vonage API key"), widget=InputWidget(), required=True)
        api_secret = forms.CharField(help_text=_("Your Vonage API secret"), widget=InputWidget(), required=True)

        def clean(self):
            super().clean()

            api_key = self.cleaned_data.get("api_key")
            api_secret = self.cleaned_data.get("api_secret")

            if not VonageClient(api_key, api_secret).check_credentials():
                raise ValidationError(_("Your API key and secret seem invalid. Please check them again and retry."))

            return self.cleaned_data

    form_class = VonageConnectForm
    permission = "channels.channel_claim"
    submit_button_name = "Save"
    success_message = "Vonage Account successfully connected."
    template_name = "channels/types/vonage/connect.html"
    menu_path = "/settings/channels/new-channel"
    title = "Connect Vonage"

    def get_success_url(self):
        return reverse("channels.types.vonage.claim")

    def pre_process(self, *args, **kwargs):
        reset_creds = self.request.GET.get("reset_creds", "")

        org = self.request.org
        last_vonage_channel = (
            org.channels.filter(is_active=True, channel_type=self.channel_type.code).order_by("-created_on").first()
        )

        if last_vonage_channel and not reset_creds:
            self.request.session[self.channel_type.SESSION_API_KEY] = last_vonage_channel.config.get(
                self.channel_type.CONFIG_API_KEY, ""
            )
            self.request.session[self.channel_type.SESSION_API_SECRET] = last_vonage_channel.config.get(
                self.channel_type.CONFIG_API_SECRET, ""
            )
            return HttpResponseRedirect(self.get_success_url())

        return None

    def form_valid(self, form):
        api_key = form.cleaned_data["api_key"]
        api_secret = form.cleaned_data["api_secret"]

        # add the credentials to the session
        self.request.session[self.channel_type.SESSION_API_KEY] = api_key
        self.request.session[self.channel_type.SESSION_API_SECRET] = api_secret

        return HttpResponseRedirect(self.get_success_url())
