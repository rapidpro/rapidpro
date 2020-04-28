from django.conf.urls import include, url

from .models import TicketService
from .views import TicketServiceCRUDL

# build up all the type specific urls
service_urls = []
for service_type in TicketService.get_types():
    urls = service_type.get_urls()
    for u in urls:
        u.name = "tickets.types.%s.%s" % (service_type.slug, u.name)

    if urls:
        service_urls.append(url("^%s/" % service_type.slug, include(urls)))

urlpatterns = [
    url(r"^", include(TicketServiceCRUDL().as_urlpatterns())),
    url(r"^tickets/types/", include(service_urls)),
]
