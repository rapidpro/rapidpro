from smartmin.views import SmartFormView

from django import forms
from django.core.exceptions import ValidationError
from django.db.models.query import Q
from django.utils.translation import ugettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        channel_id = forms.CharField(
            label=_("Channel ID"), required=True, help_text=_("The Channel ID of the LINE channel for the Bot")
        )
        name = forms.CharField(label=_("Name"), required=True, help_text=_("The Name of the Bot"))
        access_token = forms.CharField(
            label=_("Access Token"), required=True, help_text=_("The Access Token of the LINE Bot")
        )
        secret = forms.CharField(label=_("Secret"), required=True, help_text=_("The Secret of the LINE Bot"))

        def clean(self):
            access_token = self.cleaned_data.get("access_token")
            secret = self.cleaned_data.get("secret")
            channel_id = self.cleaned_data.get("channel_id")
            name = self.cleaned_data.get("name")

            credentials = {
                "channel_id": channel_id,
                "channel_access_token": access_token,
                "channel_secret": secret,
                "name": name,
            }

            existing = Channel.objects.filter(
                Q(config__contains=channel_id) | Q(config__contains=secret) | Q(config__contains=access_token),
                channel_type=self.channel_type.code,
                address=channel_id,
                is_active=True,
            ).first()
            if existing:
                raise ValidationError(_("A channel with this configuration already exists."))

            return credentials

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        credentials = form.cleaned_data
        name = credentials.get("name")
        channel_id = credentials.get("channel_id")
        channel_secret = credentials.get("channel_secret")
        channel_access_token = credentials.get("channel_access_token")

        config = {"auth_token": channel_access_token, "secret": channel_secret, "channel_id": channel_id}

        self.object = Channel.create(
            org, self.request.user, None, self.channel_type, name=name, address=channel_id, config=config
        )

        return super().form_valid(form)
