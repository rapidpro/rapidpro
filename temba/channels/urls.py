from .views import ChannelCRUDL, ChannelLogCRUDL

urlpatterns = ChannelCRUDL().as_urlpatterns()
urlpatterns += ChannelLogCRUDL().as_urlpatterns()


