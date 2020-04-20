import requests

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import TicketingService
from temba.tickets.views import BaseConnectView


class ConnectView(BaseConnectView):
    class Form(forms.Form):
        subdomain = forms.CharField(help_text=_("Your subdomain on ZenDesk"))
        username = forms.EmailField(help_text=_("Your email address on ZenDesk (without /token)"))
        api_token = forms.CharField(max_length=64, help_text=_("Your authentication token on your account"))

        def clean(self):
            cleaned = super().clean()

            if not self.is_valid():
                return cleaned

            # try to look up intents
            response = requests.get(
                "https://" + cleaned["subdomain"] + ".zendesk.com/api/v2/triggers.json",
                auth=(cleaned["username"] + "/token", cleaned["api_token"]),
            )

            if response.status_code != 200:
                raise forms.ValidationError(
                    _("Unable to get verify your username and api_token, please check them and try again")
                )

            return cleaned

    form_class = Form

    def form_valid(self, form):
        from .type import ZendeskType

        # TODO: set up trigger on Zendesk side to callback to us on ticket closures
        # See: https://developer.zendesk.com/rest_api/docs/support/triggers

        config = {
            ZendeskType.CONFIG_SUBDOMAIN: form.cleaned_data["subdomain"],
            ZendeskType.CONFIG_USERNAME: form.cleaned_data["username"],
            ZendeskType.CONFIG_API_TOKEN: form.cleaned_data["api_token"],
        }

        self.object = TicketingService.create(
            org=self.org,
            user=self.request.user,
            service_type=ZendeskType.slug,
            name=form.cleaned_data["subdomain"],
            config=config,
        )

        return super().form_valid(form)
