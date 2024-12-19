from django.urls import re_path, reverse
from django.utils.translation import gettext_lazy as _

from ...models import IntegrationType
from .views import AccountView


class DTOneType(IntegrationType):
    """
    Integration with DT One to enable sending of airtime from flows
    """

    CONFIG_KEY = "dtone_key"
    CONFIG_SECRET = "dtone_secret"

    name = "DT One"
    slug = "dtone"
    icon = "dtone"
    category = IntegrationType.Category.AIRTIME

    def connect(self, org, user, api_key: str, api_secret: str):
        org.config.update({self.CONFIG_KEY: api_key, self.CONFIG_SECRET: api_secret})
        org.modified_by = user
        org.save(update_fields=("config", "modified_by", "modified_on"))

    def is_connected(self, org) -> bool:
        return bool(org.config.get(self.CONFIG_KEY) and org.config.get(self.CONFIG_SECRET))

    def disconnect(self, org, user):
        org.config.pop(self.CONFIG_KEY, None)
        org.config.pop(self.CONFIG_SECRET, None)
        org.modified_by = user
        org.save(update_fields=("config", "modified_by", "modified_on"))

    def management_ui(self, org, formax):
        account_url = reverse("integrations.dtone.account")
        if not self.is_connected(org):
            formax.add_section(self.slug, account_url, icon=self.icon, action="redirect", button=_("Connect"))
        else:
            formax.add_section(self.slug, account_url, icon=self.icon, action="redirect", nobutton=True)

    def get_urls(self):
        return [re_path(r"^account/$", AccountView.as_view(integration_type=self), name="account")]
