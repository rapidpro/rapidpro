from django.conf import settings


def get_by_host(host: str) -> dict:
    """
    Returns the branding for the given host
    """
    for brand in settings.BRANDS:
        if host in brand["hosts"]:
            return brand

    return get_by_slug(settings.DEFAULT_BRAND)


def get_by_slug(slug: str) -> dict:
    """
    Returns the branding for the given slug
    """
    for brand in settings.BRANDS:
        if slug == brand["slug"]:
            return brand

    return get_by_slug(settings.DEFAULT_BRAND)
