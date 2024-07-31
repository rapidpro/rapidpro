from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from temba.utils.fields import InputWidget

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        title = forms.CharField(label=_("Notification Title"))

        authentication_json = forms.JSONField(
            widget=InputWidget({"textarea": True}),
            help_text=_("Copy the FCM authentication JSON file content to this field"),
            initial={},
        )
        send_notification = forms.CharField(
            label=_("Send notification"),
            required=False,
            help_text=_("Check if you want this channel to send notifications " "to contacts."),
            widget=forms.CheckboxInput(),
        )

        def clean(self):
            authentication_json = self.cleaned_data.get("authentication_json", {})

            self.cleaned_data["address"] = authentication_json.get("private_key_id", "")

            if not authentication_json or not self.cleaned_data["address"]:
                raise forms.ValidationError(_("Invalid authentication JSON, missing private_key_id field"))

            return super().clean()

    form_class = Form

    def form_valid(self, form):
        title = form.cleaned_data.get("title")
        authentication_json = form.cleaned_data.get("authentication_json")
        address = form.cleaned_data.get("address")
        config = {"FCM_TITLE": title, "FCM_CREDENTIALS_JSON": authentication_json}

        if form.cleaned_data.get("send_notification") == "True":
            config["FCM_NOTIFICATION"] = True

        self.object = Channel.create(
            self.request.org, self.request.user, None, self.channel_type, name=title, address=address, config=config
        )

        return super().form_valid(form)
