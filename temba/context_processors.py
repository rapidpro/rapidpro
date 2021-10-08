from django.conf import settings


def config(request):
    return {
        "COMPONENTS_DEV_MODE": getattr(settings, "COMPONENTS_DEV_MODE", False),
        "EDITOR_DEV_MODE": getattr(settings, "EDITOR_DEV_MODE", False),
    }


def branding(request):
    """
    Stuff our branding into the context
    """
    return dict(brand=request.branding)


def analytics(request):
    """
    Stuffs intercom / segment / google analytics settings into our request context
    """
    return dict(
        segment_key=settings.SEGMENT_IO_KEY,
        intercom_app_id=settings.INTERCOM_APP_ID,
        google_tracking_id=settings.GOOGLE_TRACKING_ID,
    )
