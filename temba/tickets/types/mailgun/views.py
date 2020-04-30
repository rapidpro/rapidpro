import requests

from django import forms
from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.utils.text import random_string

from ...models import Ticketer
from ...views import BaseConnectView


class ConnectView(BaseConnectView):
    class EmailForm(BaseConnectView.Form):
        to_address = forms.EmailField(help_text=_("The email address to forward tickets and replies to"))

    class VerifyForm(BaseConnectView.Form):
        verification_token = forms.CharField(
            max_length=6, help_text=_("The verification token that was sent to your email")
        )

        def clean_verification_token(self):
            value = self.cleaned_data["verification_token"]
            token = self.request.session.get("verification_token")
            if token == "":
                raise forms.ValidationError(_("No verification token found, please start over"))

            if token != value:
                raise forms.ValidationError(_("Token does not match, please check your email"))

            return value

    def get(self, request, *args, **kwargs):
        if not request.GET.get("verify"):
            request.session["verification_token"] = random_string(6)

            print(f"generated token: {request.session['verification_token']}")

        return super().get(request, *args, **kwargs)

    def get_form_class(self):
        return ConnectView.VerifyForm if self.request.GET.get("verify") else ConnectView.EmailForm

    def form_valid(self, form):
        from .type import MailgunType

        domain = self.org.get_branding()["ticket_email_domain"]
        api_key = settings.MAILGUN_API_KEY
        verification_code = self.request.session["verification_token"]

        # step 1, they entered their email, off to verify
        if isinstance(form, ConnectView.EmailForm):
            to_address = form.cleaned_data["to_address"]

            requests.post(
                f"https://api.mailgun.net/v3/{domain}/messages",
                files={
                    "from": (None, f"no-reply@{domain}"),
                    "to": (None, to_address),
                    "subject": (None, "Verify your email address"),
                    "text": (None, f"Your verification code is {verification_code}"),
                },
                auth=("api", api_key),
            )

            self.request.session["to_address"] = to_address
            return HttpResponseRedirect(reverse("tickets.types.mailgun.connect") + "?verify=true")

        to_address = self.request.session["to_address"]
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
