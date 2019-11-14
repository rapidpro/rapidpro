from django.conf.urls import url

from .views import LinkCRUDL, LinkHandler


urlpatterns = [url(r"^link/handler/(?P<uuid>[^/]+)/?$", LinkHandler.as_view(), {}, "links.link_handler")]

urlpatterns += LinkCRUDL().as_urlpatterns()
