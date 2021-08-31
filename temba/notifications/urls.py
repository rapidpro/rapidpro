from .views import LogCRUDL, NotificationCRUDL

urlpatterns = LogCRUDL().as_urlpatterns()
urlpatterns += NotificationCRUDL().as_urlpatterns()
