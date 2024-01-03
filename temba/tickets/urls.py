from django.conf.urls import include
from django.urls import re_path

from .views import TicketCRUDL, TopicCRUDL

urlpatterns = [
    re_path(r"^", include(TicketCRUDL().as_urlpatterns())),
    re_path(r"^", include(TopicCRUDL().as_urlpatterns())),
]
