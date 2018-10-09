from .views import BroadcastCRUDL, LabelCRUDL, MsgCRUDL

urlpatterns = MsgCRUDL().as_urlpatterns()
urlpatterns += BroadcastCRUDL().as_urlpatterns()
urlpatterns += LabelCRUDL().as_urlpatterns()
