import requests
from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.utils.fields import ExternalURLField

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        name = forms.CharField(_("Name"), max_length=30, help_text=_("Incididunt irure reprehenderit consectetur duis non."))
        socket_url = ExternalURLField(
            help_text=_("Laborum cupidatat et aliqua nisi veniam excepteur voluptate reprehenderit.")
        )

        # def clean_socket_url(self):
        #     data = self.cleaned_data["socket_url"]
        #     if data.startswith("http://"):
        #         raise Exception("It is not allowed to register a channel without an SSL certificate")

        #     if not data.startswith("https://"):
        #         data = "https://" + data

        #     return data

        def clean_socket_url(self):
            socket_url = self.cleaned_data["socket_url"]

            try:
                resp = requests.get(socket_url)
                if resp.status_code != 200:
                    raise Exception("Received non-200 response: %d", resp.status_code)

            except Exception:
                raise forms.ValidationError("Invalid URL")

            return self.cleaned_data

    form_class = Form

    def form_valid(self, form):
        from .type import CONFIG_SOCKET_URL

        org = self.request.user.get_org()
        name = form.cleaned_data["name"]
        socket_url = form.cleaned_data["socket_url"]

        config = {CONFIG_SOCKET_URL: socket_url}

        self.object = Channel.create(
            org, self.request.user, None, self.channel_type, config=config, name=name
        )

        return super().form_valid(form)