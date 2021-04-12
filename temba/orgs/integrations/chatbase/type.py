from django.conf.urls import url
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from ...models import IntegrationType
from .views import AccountView


class ChatbaseType(IntegrationType):
    """
    Integration with Chatbase to allow monitoring
    """

    CONFIG_AGENT_NAME = "CHATBASE_AGENT_NAME"
    CONFIG_API_KEY = "CHATBASE_API_KEY"
    CONFIG_VERSION = "CHATBASE_VERSION"

    name = "Chatbase"
    slug = "chatbase"
    icon = "icon-chatbase"
    category = IntegrationType.Category.MONITORING

    def is_available_to(self, user) -> bool:
        return user.is_beta()

    def connect(self, org, user, agent_name: str, api_key: str, version: str):
        org.config.update(
            {self.CONFIG_AGENT_NAME: agent_name, self.CONFIG_API_KEY: api_key, self.CONFIG_VERSION: version}
        )
        org.modified_by = user
        org.save(update_fields=("config", "modified_by", "modified_on"))

    def is_connected(self, org) -> bool:
        return bool(org.config.get(self.CONFIG_AGENT_NAME) and org.config.get(self.CONFIG_API_KEY))

    def disconnect(self, org, user):
        org.config.pop(self.CONFIG_AGENT_NAME, None)
        org.config.pop(self.CONFIG_API_KEY, None)
        org.config.pop(self.CONFIG_VERSION, None)
        org.modified_by = user
        org.save(update_fields=("config", "modified_by", "modified_on"))

    def management_ui(self, org, formax):
        account_url = reverse("integrations.chatbase.account")
        if not self.is_connected(org):
            formax.add_section(self.slug, account_url, icon=self.icon, action="redirect", button=_("Connect"))
        else:
            formax.add_section(self.slug, account_url, icon=self.icon, action="redirect", nobutton=True)

    def get_urls(self):
        return [url(r"^account$", AccountView.as_view(integration_type=self), name="account")]
