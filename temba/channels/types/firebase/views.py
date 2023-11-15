from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        title = forms.CharField(label=_("Notification Title"))
        address = forms.CharField(
            label=_("FCM Key"), help_text=_("The key provided on the the Firebase Console when you created your app.")
        )
        send_notification = forms.CharField(
            label=_("Send notification"),
            required=False,
            help_text=_("Check if you want this channel to send notifications " "to contacts."),
            widget=forms.CheckboxInput(),
        )

    form_class = Form

    def form_valid(self, form):
        title = form.cleaned_data.get("title")
        address = form.cleaned_data.get("address")
        config = {"FCM_TITLE": title, "FCM_KEY": address}

        if form.cleaned_data.get("send_notification") == "True":
            config["FCM_NOTIFICATION"] = True

        self.object = Channel.create(
            self.request.org, self.request.user, None, self.channel_type, name=title, address=address, config=config
        )

        return super().form_valid(form)
