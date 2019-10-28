from django.conf.urls import include, url

from .views import HTTPLogCRUDL

urlpatterns = [url(r"^", include(HTTPLogCRUDL().as_urlpatterns()))]
