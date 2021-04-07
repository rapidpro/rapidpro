from ...models import IntegrationType


class DTOneType(IntegrationType):
    """
    Integration with DT One to enable sending of airtime from flows
    """

    CONFIG_KEY = "dtone_key"
    CONFIG_SECRET = "dtone_secret"

    name = "DT One"
    slug = "dtone"
    icon = "icon-dtone"
    category = IntegrationType.Category.AIRTIME

    def is_connected(self, org) -> bool:
        return bool(org.config.get(self.CONFIG_KEY) and org.config.get(self.CONFIG_SECRET))

    def disconnect(self, org, user):
        org.config.pop(self.CONFIG_KEY, None)
        org.config.pop(self.CONFIG_SECRET, None)
        org.modified_by = user
        org.save(update_fields=("config", "modified_by", "modified_on"))
