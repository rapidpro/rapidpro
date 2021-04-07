import requests
from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.utils.fields import ExternalURLField

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        name = forms.CharField(label=_("Name"), max_length=64, help_text=_("Incididunt irure reprehenderit consectetur duis non."))
        base_url = ExternalURLField(
            help_text=_("Laborum cupidatat et aliqua nisi veniam excepteur voluptate reprehenderit.")
        )

    form_class = Form

    def form_valid(self, form):
        from .type import CONFIG_BASE_URL

        user = self.request.user
        org = user.get_org()

        data = form.cleaned_data

        name = form.cleaned_data["name"]
        base_url = data["base_url"]

        config = {
            CONFIG_BASE_URL: base_url
        }

        self.object = Channel.create(
            org,
            self.request.user,
            None,
            self.channel_type,
            config=config,
            name=name,
            address=name
        )

        return super().form_valid(form)