from django.conf import settings
from django.core.checks import Warning, register


@register()
def mailroom_url(app_configs, **kwargs):
    if not settings.MAILROOM_URL:
        return [
            Warning(
                "No mailroom URL set, simulation will not be available",
                hint="Set MAILROOM_URL in your Django settings.",
            )
        ]
    return []
