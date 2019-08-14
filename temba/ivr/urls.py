from django.conf.urls import url

from temba.utils.views import MailroomURLHandler

urlpatterns = [
    # register a Mailroom placeholder URL which will error if ever accessed directly
    url(
        r"^mr/ivr/c/(?P<uuid>[0-9a-f-]+)/(?P<action>handle|status|incoming)$",
        MailroomURLHandler.as_view(),
        name="mailroom.ivr_handler",
    )
]
