from ...models import TicketerType


class MailgunType(TicketerType):
    """
    Type for using mailgun as an email-based ticketer
    """

    CONFIG_DOMAIN = "domain"
    CONFIG_API_KEY = "api_key"
    CONFIG_TO_ADDRESS = "to_address"
    CONFIG_BRAND_NAME = "brand_name"
    CONFIG_URL_BASE = "url_base"

    name = "Email"
    slug = "mailgun"
    icon = "icon-email-tickets"
