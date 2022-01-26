from django.conf.urls import include
from django.urls import re_path

from .models import Ticketer
from .views import TicketCRUDL, TicketerCRUDL

# build up all the type specific urls
service_urls = []
for ticketer_type in Ticketer.get_types():
    urls = ticketer_type.get_urls()
    for u in urls:
        u.name = "tickets.types.%s.%s" % (ticketer_type.slug, u.name)

    if urls:
        service_urls.append(re_path("^%s/" % ticketer_type.slug, include(urls)))

urlpatterns = [
    re_path(r"^", include(TicketCRUDL().as_urlpatterns())),
    re_path(r"^", include(TicketerCRUDL().as_urlpatterns())),
    re_path(r"^tickets/types/", include(service_urls)),
]
