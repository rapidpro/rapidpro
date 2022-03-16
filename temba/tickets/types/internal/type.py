from ...models import TicketerType


class InternalType(TicketerType):
    """
    Type for using RapidPro itself as the ticketer.
    """

    name = "Internal"
    slug = "internal"
    icon = "icon-channel-external"

    def is_available_to(self, user):
        return False  # all orgs automatically have one and they can't connect another

    def get_urls(self):
        return []
