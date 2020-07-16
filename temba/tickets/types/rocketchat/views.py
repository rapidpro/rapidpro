from django.forms import forms
from django.utils.translation import ugettext_lazy as _

from temba.tickets.views import BaseConnectView
from temba.utils.fields import ExternalURLField


class ConnectView(BaseConnectView):
    form_blurb = _("Setup your RocketChat first to be able to integrate.")

    class Form(BaseConnectView.Form):
        domain = ExternalURLField(label=_("Domain"), help_text=_("The domain for your RocketChat installation"))

        def clean_domain(self):
            from .type import RocketChatType

            org = self.request.user.get_org()
            data = self.cleaned_data["domain"]

            for_domain = org.ticketers.filter(
                is_active=True, ticketer_type=RocketChatType.slug, config__domain=data
            )
            if for_domain.exists():
                raise forms.ValidationError(_("There is already a ticketing service configured for this domain."))

            return data

    form_class = Form
    template_name = "tickets/types/rocketchat/connect.haml"
