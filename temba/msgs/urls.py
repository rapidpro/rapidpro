from .views import BroadcastCRUDL, LabelCRUDL, MediaCRUDL, MsgCRUDL

urlpatterns = MsgCRUDL().as_urlpatterns()
urlpatterns += BroadcastCRUDL().as_urlpatterns()
urlpatterns += LabelCRUDL().as_urlpatterns()
urlpatterns += MediaCRUDL().as_urlpatterns()
