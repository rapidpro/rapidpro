from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        address = forms.CharField(label=_("Domain"), help_text=_("The email domain you have configured in Mailgun."))
        sending_key = forms.CharField(
            label=_("Sending API key"),
            help_text=_("A sending API key you have configured for this domain."),
            max_length=50,
        )

    form_class = Form

    def form_valid(self, form):
        domain = form.cleaned_data["address"]

        self.object = Channel.create(
            self.request.org,
            self.request.user,
            None,
            self.channel_type,
            name=f"Mailgun: {domain}",
            address=domain,
            config={Channel.CONFIG_AUTH_TOKEN: form.cleaned_data["sending_key"]},
        )

        return super().form_valid(form)
