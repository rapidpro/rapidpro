from django.conf import settings


def config(request):
    return {
        "COMPONENTS_DEV_MODE": getattr(settings, "COMPONENTS_DEV_MODE", False),
        "EDITOR_DEV_MODE": getattr(settings, "EDITOR_DEV_MODE", False),
        "google_tracking_id": settings.GOOGLE_TRACKING_ID,
    }


def branding(request):
    """
    Stuff our branding into the context
    """
    return dict(brand=request.branding)
