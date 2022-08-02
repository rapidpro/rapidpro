from .views import IncidentCRUDL, NotificationCRUDL

urlpatterns = IncidentCRUDL().as_urlpatterns()
urlpatterns += NotificationCRUDL().as_urlpatterns()
