from django.conf import settings


def use_weni_layout(request):

    host = request.get_host().split(":")[0]

    return {"use_weni_layout": host.endswith(settings.WENI_DOMAINS["weni"])}


def show_sidemenu(request):
    if request.path == "/":
        return {"show_sidemenu": False}

    for path in settings.SIDEBAR_EXCLUDE_PATHS:
        if path in request.path:
            return {"show_sidemenu": False}

    return {"show_sidemenu": True}


def weni_announcement(request):
    return {
        "announcement_left": settings.ANNOUNCEMENT_LEFT,
        "announcement_right": settings.ANNOUNCEMENT_RIGHT,
        "announcement_link": settings.ANNOUNCEMENT_LINK,
        "announcement_button": settings.ANNOUNCEMENT_BUTTON,
    }


def hotjar(request):
    domain = '.'.join(request.get_host().split(":")[0].split('.')[-2:])
    return {"hotjar_id": settings.HOTJAR_IDS.get(domain, '')}
