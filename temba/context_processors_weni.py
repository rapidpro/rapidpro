from django.conf import settings


def enable_weni_layout(request):

    host = request.get_host().split(":")[0]

    return {"use_weni_layout": host.endswith(settings.WENI_DOMAINS["weni"])}


def weni_announcement(request):
    return {
        "announcement_left": settings.ANNOUNCEMENT_LEFT,
        "announcement_right": settings.ANNOUNCEMENT_RIGHT,
        "announcement_link": settings.ANNOUNCEMENT_LINK,
        "announcement_button": settings.ANNOUNCEMENT_BUTTON,
    }


def hotjar(request):
    return {"hotjar_id": settings.HOTJAR_ID}
