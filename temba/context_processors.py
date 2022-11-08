from django.conf import settings

from temba.utils import Icon


def config(request):
    return {
        "COMPONENTS_DEV_MODE": getattr(settings, "COMPONENTS_DEV_MODE", False),
        "EDITOR_DEV_MODE": getattr(settings, "EDITOR_DEV_MODE", False),
        "google_tracking_id": settings.GOOGLE_TRACKING_ID,
        "Icon": Icon,
    }


def branding(request):
    """
    Stuff our branding into the context
    """
    return dict(brand=request.branding)
