from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        from_addr = forms.EmailField(label=_("From Address"), help_text=_("The from address messages will come from."))
        subject = forms.CharField(label=_("Subject"), help_text=_("The default subject for new emails."))
        sending_key = forms.CharField(
            label=_("Sending API key"),
            help_text=_("A sending API key you have configured for this domain."),
            max_length=50,
        )
        signing_key = forms.CharField(
            label=_("Webhook Signing key"),
            help_text=_("The signing key used for webhook calls."),
            max_length=50,
        )

    form_class = Form

    def derive_initial(self):
        return {"subject": f"Chat with {self.request.org.name}"}

    def form_valid(self, form):
        from .type import MailgunType

        from_addr = form.cleaned_data["from_addr"]
        _, domain = from_addr.split("@", 1)

        self.object = Channel.create(
            self.request.org,
            self.request.user,
            None,
            self.channel_type,
            name=f"Mailgun: {domain}",
            address=domain,
            config={
                Channel.CONFIG_AUTH_TOKEN: form.cleaned_data["sending_key"],
                MailgunType.CONFIG_FROM_ADDRESS: from_addr,
                MailgunType.CONFIG_DEFAULT_SUBJECT: form.cleaned_data["subject"],
                MailgunType.CONFIG_SIGNING_KEY: form.cleaned_data["signing_key"],
            },
        )

        return super().form_valid(form)
