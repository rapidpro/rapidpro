# -*- coding: utf-8 -*-
from .views import MsgCRUDL, BroadcastCRUDL, LabelCRUDL

urlpatterns = MsgCRUDL().as_urlpatterns()
urlpatterns += BroadcastCRUDL().as_urlpatterns()
urlpatterns += LabelCRUDL().as_urlpatterns()
