from ...models import TicketerType


class InternalType(TicketerType):
    """
    Type for using RapidPro itself as the ticketer. It doesn't have connect views because users can't create an instance
    themselves - every org gets one on initialization.
    """

    name = "Internal"
    slug = "internal"

    def is_available_to(self, user):
        return False

    def get_urls(self):
        return []
