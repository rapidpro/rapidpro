from django.conf import settings
from django.conf.urls import include
from django.conf.urls.static import static
from django.urls import re_path
from django.views.i18n import JavaScriptCatalog

# javascript translation packages
js_info_dict = {"packages": ()}  # this is empty due to the fact that all translation are in one folder

urlpatterns = []
# import any additional urls
for app in settings.APP_URLS:  # pragma: needs cover
    urlpatterns.append(re_path(r"^", include(app)))

urlpatterns += [
    re_path(r"^", include("temba.airtime.urls")),
    re_path(r"^", include("temba.api.urls")),
    re_path(r"^", include("temba.apks.urls")),
    re_path(r"^", include("temba.archives.urls")),
    re_path(r"^", include("temba.campaigns.urls")),
    re_path(r"^", include("temba.channels.urls")),
    re_path(r"^", include("temba.classifiers.urls")),
    re_path(r"^", include("temba.contacts.urls")),
    re_path(r"^", include("temba.dashboard.urls")),
    re_path(r"^", include("temba.flows.urls")),
    re_path(r"^", include("temba.globals.urls")),
    re_path(r"^", include("temba.ivr.urls")),
    re_path(r"^", include("temba.locations.urls")),
    re_path(r"^", include("temba.msgs.urls")),
    re_path(r"^", include("temba.notifications.urls")),
    re_path(r"^", include("temba.public.urls")),
    re_path(r"^", include("temba.request_logs.urls")),
    re_path(r"^", include("temba.templates.urls")),
    re_path(r"^", include("temba.tickets.urls")),
    re_path(r"^", include("temba.triggers.urls")),
    re_path(r"^", include("temba.orgs.urls")),
    re_path(r"^staff/", include("temba.staff.urls")),
    re_path(r"^jsi18n/$", JavaScriptCatalog.as_view(), js_info_dict, name="django.views.i18n.javascript_catalog"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
