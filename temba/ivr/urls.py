from django.urls import re_path

from temba.utils.views import MailroomURLHandler

urlpatterns = [
    # register a Mailroom placeholder URL which will error if ever accessed directly
    re_path(
        r"^mr/ivr/c/(?P<uuid>[0-9a-f-]+)/(?P<action>handle|status|incoming)$",
        MailroomURLHandler.as_view(),
        name="mailroom.ivr_handler",
    )
]
