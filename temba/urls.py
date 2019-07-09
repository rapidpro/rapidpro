import importlib

from django.conf import settings
from django.conf.urls import include, url
from django.conf.urls.static import static
from django.contrib.auth.models import AnonymousUser, User
from django.views.generic import RedirectView
from django.views.i18n import JavaScriptCatalog
from temba.triggers import views

from celery.signals import worker_process_init

from temba.channels.views import register, sync
from temba.utils.analytics import init_analytics

# javascript translation packages
js_info_dict = {"packages": ()}  # this is empty due to the fact that all translation are in one folder

VHOST_NAME = ""
if hasattr(settings, 'SUB_DIR'):
    if settings.SUB_DIR[-1:] == "/":
        VHOST_NAME = settings.SUB_DIR
    else:
        VHOST_NAME = settings.SUB_DIR + "/"

urlpatterns = [
    url(r"^{}".format(VHOST_NAME), include("temba.public.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.msgs.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.apks.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.archives.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.policies.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.contacts.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.orgs.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.schedules.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.flows.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.triggers.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.campaigns.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.ivr.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.locations.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.api.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.channels.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.airtime.urls")),
    url(r"^{}".format(VHOST_NAME), include("temba.dashboard.urls")),
    url(r"^{}relayers/relayer/sync/(\d+)/$".format(VHOST_NAME), sync, {}, "sync"),
    url(r"^{}relayers/relayer/register/$".format(VHOST_NAME), register, {}, "register"),
    url(r"^{}users/".format(VHOST_NAME), include("smartmin.users.urls")),
    url(r"^{}imports/".format(VHOST_NAME), include("smartmin.csv_imports.urls")),
    url(r"^{}assets/".format(VHOST_NAME), include("temba.assets.urls")),
    url(r"^{}jsi18n/$".format(VHOST_NAME), JavaScriptCatalog.as_view(), js_info_dict,
        name="django.views.i18n.javascript_catalog"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


# import any additional urls
for app in settings.APP_URLS:  # pragma: needs cover
    importlib.import_module(app)

# initialize our analytics (the signal below will initialize each worker)
init_analytics()


@worker_process_init.connect
def configure_workers(sender=None, **kwargs):
    init_analytics()  # pragma: needs cover


def track_user(self):  # pragma: no cover
    """
    Should the current user be tracked
    """

    # don't track unless we are on production
    if not settings.IS_PROD:
        return False

    # nothing to report if they haven't logged in
    if not self.is_authenticated or self.is_anonymous:
        return False

    return True


User.track_user = track_user
AnonymousUser.track_user = track_user


def handler500(request):
    """
    500 error handler which includes ``request`` in the context.

    Templates: `500.html`
    Context: None
    """
    from django.template import loader
    from django.http import HttpResponseServerError

    t = loader.get_template("500.html")
    return HttpResponseServerError(t.render({"request": request}))  # pragma: needs cover
