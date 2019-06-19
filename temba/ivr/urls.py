from django.conf.urls import url

from .views import CallHandler, MailroomHandler

urlpatterns = [url(r"^handle/(?P<pk>\d+)/$", CallHandler.as_view(), name="ivr.ivrcall_handle")]
urlpatterns += [
    url(
        r"^mr/ivr/c/(?P<uuid>[0-9a-f-]+)/(?P<action>handle|status|incoming)$",
        MailroomHandler.as_view(),
        name="mailroom.ivr_handler",
    )
]
