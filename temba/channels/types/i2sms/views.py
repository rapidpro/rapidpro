from django import forms
from django.utils.translation import ugettext_lazy as _

from ...views import AuthenticatedExternalClaimView


class ClaimView(AuthenticatedExternalClaimView):
    class Form(AuthenticatedExternalClaimView.Form):
        channel_hash = forms.CharField(max_length=42, help_text=_("The hash of your i2SMS channel"))
        username = forms.CharField(label=_("Username"), help_text=_("Your i2SMS username"))
        password = forms.CharField(label=_("Password"), help_text=_("Your i2SMS password"))

    form_class = Form

    def get_channel_config(self, org, data):
        return dict(channel_hash=data["channel_hash"])
