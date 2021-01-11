from urllib.parse import urljoin

import phonenumbers
import requests
from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.utils.fields import ExternalURLField

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        api_endpoint = ExternalURLField(
            label=_("API Endpoint"), help_text=_("The API endpoint for your TextIt WhatsApp number")
        )
        access_token = forms.CharField(
            label=_("Access Token"), max_length=256, help_text=_("The access token for your TextIt WhatsApp number")
        )

        def clean(self):
            cleaned = self.cleaned_data

            cleaned["api_endpoint"] = urljoin(cleaned["api_endpoint"], "/")
            headers = {"Authorization": f"Bearer {cleaned['access_token']}", "Content-Type": "application/json"}

            conf_url = urljoin(cleaned["api_endpoint"], "/conf")
            response = requests.get(conf_url, headers=headers)
            if response.status_code != 200:
                raise forms.ValidationError("Error reaching endpoint, please check access token and URL")

            conf = response.json()

            if conf["status"] != "activated":
                raise forms.ValidationError("WhatsApp number not active, cannot connect")

            self.cleaned_data["address"] = conf["address"]
            self.cleaned_data["country"] = conf["country"]
            self.cleaned_data["name"] = conf["name"]

            return self.cleaned_data

    form_class = Form

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()

        data = form.cleaned_data

        config = {Channel.CONFIG_BASE_URL: data["api_endpoint"], Channel.CONFIG_AUTH_TOKEN: data["access_token"]}

        parsed = phonenumbers.parse(data["address"], data["country"])
        pretty = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)

        self.object = Channel.create(
            org,
            user,
            data["country"],
            self.channel_type,
            name=f"{data['name']}: {pretty}",
            address=data["address"],
            config=config,
            tps=10,
        )

        return super().form_valid(form)
