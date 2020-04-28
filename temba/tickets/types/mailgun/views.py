from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import TicketService
from temba.tickets.views import BaseConnectView


class ConnectView(BaseConnectView):
    class Form(forms.Form):
        def __init__(self, request, *args, **kwargs):
            self.request = request
            super().__init__(*args, **kwargs)

        domain = forms.CharField(help_text=_("The email domain"))
        api_token = forms.CharField(max_length=64, help_text=_("Your API token on your account"))
        to_address = forms.EmailField(help_text=_("The email address to forward tickets and replies to"))

        def clean(self):
            cleaned = super().clean()

            if not self.is_valid():
                return cleaned

            # TODO verify credentials

            # TODO verify ownership of to address?

            return cleaned

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

        self.object = TicketService.create(
            org=self.org,
            user=self.request.user,
            service_type=MailgunType.slug,
            config=config,
            name=f"Mailgun ({to_address})",
        )

        return super().form_valid(form)
