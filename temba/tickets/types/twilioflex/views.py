import requests
from django import forms

from django.forms import ValidationError
from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import Ticketer
from temba.tickets.views import BaseConnectView
from temba.utils.uuid import uuid4


class ConnectView(BaseConnectView):
    class Form(BaseConnectView.Form):
        ticketer_name = forms.CharField(
            label=_("Ticketer Name"),
            help_text=_("A name to help identify your ticketer"),
        )
        account_sid = forms.CharField(
            label=_("Authentication User"),
            help_text=_("Account SID or API Key SID of a twilio account."),
        )
        auth_token = forms.CharField(
            label=_("Authentication Password"),
            help_text=_("Auth token or API Key Secret of a twilio account."),
        )
        chat_service_sid = forms.CharField(
            label=_("Chat Service SID"), help_text=_("SID of a Chat service instance.")
        )
        flex_flow_sid = forms.CharField(
            label=_("Flex Flow"),
            help_text=_(
                "a Flex Flow (also called Flex Address) that is of the task type."
            ),
        )
        workspace_sid = forms.CharField(
            label=_("Workspace SID"), help_text=_("SID of a Workspace.")
        )

        def clean(self):
            account_sid = self.cleaned_data["account_sid"]
            auth_token = self.cleaned_data["auth_token"]
            chat_service_sid = self.cleaned_data["chat_service_sid"]

            try:
                response = requests.get(
                    f"https://chat.twilio.com/v2/Services/{chat_service_sid}",
                    auth=(account_sid, auth_token),
                )
                print(response.status_code)
                response.raise_for_status()

            except Exception:
                raise ValidationError(
                    _(
                        "Unable to connect with twilio chat service, please check input fields and try again."
                    )
                )
            return self.cleaned_data

    def form_valid(self, form):
        from .type import TwilioFlexType

        ticketer_name = form.cleaned_data["ticketer_name"]
        account_sid = form.cleaned_data["account_sid"]
        auth_token = form.cleaned_data["auth_token"]
        chat_service_sid = form.cleaned_data["chat_service_sid"]
        flex_flow_sid = form.cleaned_data["flex_flow_sid"]
        workspace_sid = form.cleaned_data["workspace_sid"]

        config = {
            TwilioFlexType.CONFIG_ACCOUNT_SID: account_sid,
            TwilioFlexType.CONFIG_AUTH_TOKEN: auth_token,
            TwilioFlexType.CONFIG_CHAT_SERVICE_SID: chat_service_sid,
            TwilioFlexType.CONFIG_FLEX_FLOW_SID: flex_flow_sid,
            TwilioFlexType.CONFIG_WORKSPACE_SID: workspace_sid,
        }

        self.object = Ticketer(
            uuid=uuid4(),
            org=self.org,
            ticketer_type=TwilioFlexType.slug,
            config=config,
            name=ticketer_name,
            created_by=self.request.user,
            modified_by=self.request.user,
        )
        self.object.save()
        return super().form_valid(form)

    form_class = Form
    template_name = "tickets/types/twilioflex/connect.haml"
