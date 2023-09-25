from ...models import TicketerType


class InternalType(TicketerType):
    """
    Type for using RapidPro itself as the ticketer.
    """

    name = "Internal"
    slug = "internal"

    def get_urls(self):
        return []
