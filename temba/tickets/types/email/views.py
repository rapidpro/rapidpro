import random
import string

from django import forms
from django.forms import ValidationError
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import TicketingService
from temba.tickets.views import BaseConnectView


class ConnectView(BaseConnectView):
    class EmailForm(forms.Form):
        def __init__(self, request, *args, **kwargs):
            self.request = request
            super().__init__(*args, **kwargs)

        email_address = forms.EmailField(help_text=_("The email address to forward tickets and replies to"))

    class TokenForm(forms.Form):
        def __init__(self, request, *args, **kwargs):
            self.request = request
            super().__init__(*args, **kwargs)

        verification_token = forms.CharField(
            max_length=6, help_text=_("The verification token that was sent to your email")
        )

        def clean_verification_token(self):
            value = self.cleaned_data["verification_token"]
            token = self.request.session.get("verification_token")
            if token == "":
                raise ValidationError(_("No verification token found, please start over"))

            if token != value:
                raise ValidationError(_("Token does not match, please check your email"))

            return value

        def clean(self):
            email = self.request.session.get("email_address")
            if email is None:
                raise ValidationError(_("No email address found, please start over"))

            self.cleaned_data["email_address"] = email
            return self.cleaned_data

    def get_form_class(self):
        if self.request.GET.get("verify"):
            return ConnectView.TokenForm
        else:
            return ConnectView.EmailForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get(self, request, *args, **kwargs):
        if request.GET.get("verify") is None:
            token = "".join(random.choice(string.ascii_lowercase) for i in range(6))
            request.session["verification_token"] = token
            print(f"generated token: {token}")

        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        # step 1, they entered their email, off to verify
        if isinstance(form, ConnectView.EmailForm):
            self.request.session["email_address"] = form.cleaned_data["email_address"]

            # TODO: send actual email

            return HttpResponseRedirect(reverse("tickets.types.email.connect") + "?verify=true")

        from .type import EmailType

        config = {EmailType.CONFIG_EMAIL_ADDRESS: form.cleaned_data["email_address"]}

        self.object = TicketingService.create(
            org=self.org,
            user=self.request.user,
            service_type=EmailType.slug,
            config=config,
            name=form.cleaned_data["email_address"],
        )

        return super().form_valid(form)
