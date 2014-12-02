from .views import MsgCRUDL, BroadcastCRUDL, CallCRUDL, LabelCRUDL

urlpatterns = MsgCRUDL().as_urlpatterns()
urlpatterns += BroadcastCRUDL().as_urlpatterns()
urlpatterns += CallCRUDL().as_urlpatterns()
urlpatterns += LabelCRUDL().as_urlpatterns()
