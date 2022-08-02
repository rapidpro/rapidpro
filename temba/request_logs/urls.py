from django.conf.urls import include
from django.urls import re_path

from .views import HTTPLogCRUDL

urlpatterns = [re_path(r"^", include(HTTPLogCRUDL().as_urlpatterns()))]
