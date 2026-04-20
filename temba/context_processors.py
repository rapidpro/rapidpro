from django.conf import settings


def config(request):
    return {
        "COMPONENTS_DEV_MODE": getattr(settings, "COMPONENTS_DEV_MODE", False),
        "EDITOR_DEV_MODE": getattr(settings, "EDITOR_DEV_MODE", False),
        "EDITOR_DEV_HOST": getattr(settings, "EDITOR_DEV_HOST", "localhost"),
        "COMPONENTS_DEV_HOST": getattr(settings, "COMPONENTS_DEV_HOST", "localhost"),
    }


def branding(request):
    """
    Stuff our branding into the context
    """
    return dict(branding=request.branding)
