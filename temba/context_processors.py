from django.conf import settings


def branding(request):
    """
    Stuff our branding into the context
    """
    return dict(
        brand=request.branding,
        recaptcha_site_key=settings.RECAPTCHA_SITE_KEY,
        recaptcha_secrete_key=settings.RECAPTCHA_SECRET_KEY
    )
