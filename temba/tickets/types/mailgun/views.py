import requests

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import Ticketer
from temba.tickets.views import BaseConnectView


class ConnectView(BaseConnectView):
    class Form(BaseConnectView.Form):
        domain = forms.CharField(help_text=_("The email domain name"))
        api_key = forms.CharField(max_length=64, label=_("API Key"), help_text=_("Your private API key"))
        to_address = forms.EmailField(help_text=_("The email address to forward tickets and replies to"))

        def clean(self):
            cleaned = super().clean()

            if not self.is_valid():
                return cleaned

            # ping their API to see if we can authenticate
            response = requests.get(
                f"https://api.mailgun.net/v3/{cleaned['domain']}/log", auth=("api", cleaned["api_key"])
            )
            if response.status_code >= 400:
                raise forms.ValidationError(
                    _("Unable to get verify your domain and API key, please check them and try again")
                )

            return cleaned

    form_class = Form

    def form_valid(self, form):
        from .type import MailgunType

        domain = form.cleaned_data["domain"]
        api_key = form.cleaned_data["api_key"]
        to_address = form.cleaned_data["to_address"]

        config = {
            MailgunType.CONFIG_DOMAIN: domain,
            MailgunType.CONFIG_API_KEY: api_key,
            MailgunType.CONFIG_TO_ADDRESS: to_address,
        }

        self.object = Ticketer.create(
            org=self.org,
            user=self.request.user,
            ticketer_type=MailgunType.slug,
            config=config,
            name=f"Mailgun ({to_address})",
        )

        return super().form_valid(form)
