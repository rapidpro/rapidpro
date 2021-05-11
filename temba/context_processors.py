from django.conf import settings


def branding(request):
    """
    Stuff our branding into the context
    """
    if "vanilla" in request.GET:  # pragma: no cover
        request.session["vanilla"] = request.GET.get("vanilla")

    return dict(brand=request.branding, vanilla=request.session.get("vanilla", "0") == "1")


def analytics(request):
    """
    Stuffs intercom / segment / google analytics settings into our request context
    """
    return dict(
        segment_key=settings.SEGMENT_IO_KEY,
        intercom_app_id=settings.INTERCOM_APP_ID,
        google_tracking_id=settings.GOOGLE_TRACKING_ID,
    )
