from django.conf import settings


def get_branding_by_host(host: str) -> dict:
    """
    Returns the branding for the given host
    """
    for brand in settings.BRANDS:
        if host in brand["hosts"]:
            return brand

    return get_branding_by_slug(settings.DEFAULT_BRAND)


def get_branding_by_slug(slug: str) -> dict:
    """
    Returns the branding for the given host or alias
    """
    for brand in settings.BRANDS:
        if slug == brand["slug"]:
            return brand

    # TODO update orgs and DEFAULT_BRAND to use brand slugs rather than hosts
    return get_branding_by_host(slug)
