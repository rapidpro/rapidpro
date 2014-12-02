from django.conf.urls import patterns
from .views import *

urlpatterns = ReportCRUDL().as_urlpatterns()

