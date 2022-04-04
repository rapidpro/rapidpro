from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import Ticketer
from temba.tickets.views import BaseConnectView
from temba.utils.uuid import uuid4


class ConnectView(BaseConnectView):
    class Form(BaseConnectView.Form):
        account_sid = forms.CharField(
            label=_("Account SID"), help_text=_("ACCOUNT SID of a twilio account organization.")
        )
        auth_token = forms.CharField(
            label=_("Auth Token"), help_text=_("Auth token of a twilio account organization.")
        )
        chat_service_sid = forms.CharField(label=_("Chat Service SID"), help_text=_("SID of a Chat service."))
        flex_flow_sid = forms.CharField(
            label=_("Flex Flow"),
            help_text=_("SID of a Flex Flow (also called Flex Address) that is of the task type."),
        )
        workspace_sid = forms.CharField(label=_("Workspace SID"), help_text=_("SID of a Workspace."))

    def form_valid(self, form):
        from .type import TwilioFlexType

        config = {
            TwilioFlexType.CONFIG_ACCOUNT_SID: form.cleaned_data["account_sid"],
            TwilioFlexType.CONFIG_AUTH_TOKEN: form.cleaned_data["auth_token"],
            TwilioFlexType.CONFIG_CHAT_SERVICE_SID: form.cleaned_data["chat_service_sid"],
            TwilioFlexType.CONFIG_FLEX_FLOW_SID: form.cleaned_data["flex_flow_sid"],
            TwilioFlexType.CONFIG_WORKSPACE_SID: form.cleaned_data["workspace_sid"],
        }
        self.object = Ticketer(
            uuid=uuid4(),
            org=self.org,
            ticketer_type=TwilioFlexType.slug,
            config=config,
            name=TwilioFlexType.name,
            created_by=self.request.user,
            modified_by=self.request.user,
        )
        self.object.save()
        return super().form_valid(form)

    form_class = Form
    template_name = "tickets/types/twilioflex/connect.haml"
