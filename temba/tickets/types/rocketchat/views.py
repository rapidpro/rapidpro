from django.forms import forms
from django.utils.translation import ugettext_lazy as _

from temba.tickets.views import BaseConnectView
from temba.utils.fields import ExternalURLField


class ConnectView(BaseConnectView):
    form_blurb = _("Setup your RocketChat first to be able to integrate.")

    class Form(BaseConnectView.Form):
        base_url = ExternalURLField(label=_("Base URL"), help_text=_("The base URL for your RocketChat installation"))

        def clean_base_url(self):
            from .type import RocketChatType

            org = self.request.user.get_org()
            data = self.cleaned_data["base_url"]

            for_base_url = org.ticketers.filter(
                is_active=True, ticketer_type=RocketChatType.slug, config__base_url=data
            )
            if for_base_url.exists():
                raise forms.ValidationError(_("There is already a ticketing service configured for this base URL."))

            return data

    form_class = Form
    template_name = "tickets/types/rocketchat/connect.haml"
