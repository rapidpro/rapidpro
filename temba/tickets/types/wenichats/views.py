from django import forms
from django.utils.translation import ugettext_lazy as _
from temba.api.models import APIToken

from temba.tickets.models import Ticketer
from temba.tickets.views import BaseConnectView
from temba.utils.uuid import uuid4
from django.core.exceptions import ValidationError
import requests


class ConnectView(BaseConnectView):
    class Form(BaseConnectView.Form):
        project_auth = forms.CharField(
            label=_("Project Auth"), help_text=_("Project Auth")
        )
        sector_uuid = forms.CharField(
            label=_("Sector UUID"), help_text=_("Sector UUID")
        )

        def clean(self):
            from .type import WeniChatsType

            sector_uuid = self.cleaned_data.get("sector_uuid")
            if not sector_uuid:
                raise forms.ValidationError(_("Invalid sector UUID"))

            existing = Ticketer.objects.filter(
                is_active=True,
                ticketer_type=WeniChatsType.slug,
                config__contains=sector_uuid,
            )

            if existing:
                if existing.org_id == self.request.user.get_org().id:
                    raise ValidationError(
                        _(
                            "A Weni Chats ticketer for this sector already exists in this workspace."
                        )
                    )
                raise ValidationError(
                    _(
                        "A Weni Chats ticketer for this sector already exists in another workspace."
                    )
                )

    def form_valid(self, form):
        from .type import WeniChatsType

        sector_uuid = form.cleaned_data["sector_uuid"]

        project_auth = form.cleaned_data["project_auth"]

        sectors_response = requests.get(
            url=WeniChatsType.base_url + "/sectors/",
            headers={"Authorization": "Bearer " + project_auth},
        )

        if sectors_response.status_code != 200:
            raise Exception(
                _(
                    "This ticketer integration with Weni Chats couldn't be created, check if all fields is correct and try again."
                )
            )

        current_sector = {}

        for sector in sectors_response.json()["results"]:
            if sector["uuid"] == sector_uuid:
                current_sector = sector

        if not current_sector:
            raise Exception(
                _(
                    "This ticketer integration with Weni Chats couldn't be created, the defined sector not exists."
                )
            )

        config = {
            WeniChatsType.CONFIG_SECTOR_UUID: sector_uuid,
            WeniChatsType.CONFIG_PROJECT_AUTH: project_auth,
        }

        self.object = Ticketer(
            uuid=uuid4(),
            org=self.org,
            ticketer_type=WeniChatsType.slug,
            config=config,
            name=current_sector["name"],
            created_by=self.request.user,
            modified_by=self.request.user,
        )

        self.object.save()
        return super().form_valid(form)

    form_class = Form
    template_name = "tickets/types/wenichats/connect.haml"
