import copy

from django.conf import settings


def get_by_host(host: str) -> dict:
    """
    Returns the branding for the given host
    """
    return copy.deepcopy(settings.BRAND)
