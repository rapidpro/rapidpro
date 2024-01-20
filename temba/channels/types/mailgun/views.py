from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        address = forms.EmailField(label=_("Email Address"), help_text=_("The email address."))
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

        address = form.cleaned_data["address"]

        self.object = Channel.create(
            self.request.org,
            self.request.user,
            None,
            self.channel_type,
            name=address,
            address=address,
            config={
                Channel.CONFIG_AUTH_TOKEN: form.cleaned_data["sending_key"],
                MailgunType.CONFIG_DEFAULT_SUBJECT: form.cleaned_data["subject"],
                MailgunType.CONFIG_SIGNING_KEY: form.cleaned_data["signing_key"],
            },
        )

        return super().form_valid(form)
