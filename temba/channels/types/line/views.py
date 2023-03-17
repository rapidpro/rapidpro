from smartmin.views import SmartFormView

from django import forms
from django.core.exceptions import ValidationError
from django.db.models.query import Q
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        channel_id = forms.CharField(
            label=_("Channel ID"), required=True, help_text=_("Channel ID of the LINE channel for the bot.")
        )
        name = forms.CharField(label=_("Name"), max_length=64, required=True, help_text=_("Name of the bot."))
        access_token = forms.CharField(label=_("Access Token"), required=True, help_text=_("Access token of the bot."))
        secret = forms.CharField(label=_("Secret"), required=True, help_text=_("Secret of the bot."))

        def clean(self):
            cleaned_data = super().clean()

            channel_id = cleaned_data.get("channel_id")
            access_token = cleaned_data.get("access_token")
            secret = cleaned_data.get("secret")

            existing = Channel.objects.filter(
                Q(config__channel_id=channel_id) | Q(config__secret=secret) | Q(config__auth_token=access_token),
                channel_type=self.channel_type.code,
                address=channel_id,
                is_active=True,
            ).exists()
            if existing:
                raise ValidationError(_("A channel with this configuration already exists."))

    form_class = Form

    def form_valid(self, form):
        name = form.cleaned_data.get("name")
        channel_id = form.cleaned_data.get("channel_id")
        secret = form.cleaned_data.get("secret")
        access_token = form.cleaned_data.get("access_token")

        config = {"auth_token": access_token, "secret": secret, "channel_id": channel_id}

        self.object = Channel.create(
            self.request.org, self.request.user, None, self.channel_type, name=name, address=channel_id, config=config
        )

        return super().form_valid(form)
