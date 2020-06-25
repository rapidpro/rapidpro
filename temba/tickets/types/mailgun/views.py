from django import forms
from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.html import escape
from django.utils.translation import ugettext_lazy as _

from temba.utils.email import send_template_email
from temba.utils.text import random_string

from ...models import Ticketer
from ...views import BaseConnectView


class ConnectView(BaseConnectView):
    class EmailForm(BaseConnectView.Form):
        to_address = forms.EmailField(
            label=_("Address"), help_text=_("The email address to forward tickets and replies to")
        )

    class VerifyForm(BaseConnectView.Form):
        verification_code = forms.CharField(
            max_length=6, help_text=_("The verification code that was sent to your email")
        )

        def clean_verification_code(self):
            value = self.cleaned_data["verification_code"]
            code = self.request.session.get("verification_code")
            if not code:
                raise forms.ValidationError(_("No verification code found, please start over."))
            if code != value:
                raise forms.ValidationError(_("Code does not match, please check your email."))

            return value

    def is_verify_step(self):
        return "verify" in self.request.GET

    def get(self, request, *args, **kwargs):
        if not self.is_verify_step():
            request.session["verification_code"] = random_string(6)

        return super().get(request, *args, **kwargs)

    def derive_title(self):
        return _("Verify Email") if self.is_verify_step() else super().derive_title()

    def get_form_class(self):
        return ConnectView.VerifyForm if self.is_verify_step() else ConnectView.EmailForm

    def get_form_blurb(self):
        if self.is_verify_step():
            address = escape(self.request.session["to_address"])
            return _(
                "A verification code was sent to <code>%(address)s</code>. Enter it below to continue adding this "
                "ticketing service to your account."
            ) % {"address": address}
        else:
            return _(
                "New tickets and replies will be sent to the email address that you configure below. "
                "You will need to verify it by entering the code sent to you."
            )

    def form_valid(self, form):
        from .type import MailgunType

        branding = self.org.get_branding()
        domain = self.org.get_branding()["ticket_domain"]
        api_key = settings.MAILGUN_API_KEY
        verification_code = self.request.session["verification_code"]

        # step 1, they entered their email, off to verify
        if isinstance(form, ConnectView.EmailForm):
            to_address = form.cleaned_data["to_address"]
            subject = _("Verify your email address for tickets")
            template = "tickets/types/mailgun/verify_email"
            context = {"verification_code": verification_code}
            send_template_email(to_address, subject, template, context, self.request.branding)

            self.request.session["to_address"] = to_address
            return HttpResponseRedirect(reverse("tickets.types.mailgun.connect") + "?verify=true")

        # delete code so it can't be re-used
        del self.request.session["verification_code"]

        to_address = self.request.session["to_address"]
        config = {
            MailgunType.CONFIG_DOMAIN: domain,
            MailgunType.CONFIG_API_KEY: api_key,
            MailgunType.CONFIG_TO_ADDRESS: to_address,
            MailgunType.CONFIG_BRAND_NAME: branding["name"],
            MailgunType.CONFIG_URL_BASE: branding["link"],
        }

        self.object = Ticketer.create(
            org=self.org,
            user=self.request.user,
            ticketer_type=MailgunType.slug,
            config=config,
            name=f"Email ({to_address})",
        )

        return super().form_valid(form)
