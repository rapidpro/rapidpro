from django.conf import settings


def get_branding(host_or_alias: str) -> dict:
    """
    Returns the branding for the given host or alias
    """
    for brand in settings.BRANDS:
        if host_or_alias == brand["host"]:
            return brand
        for alias in brand.get("aliases", []):
            if host_or_alias == alias:
                return brand

    return get_branding(settings.DEFAULT_BRAND)
