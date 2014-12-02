from .views import *

urlpatterns = ChannelCRUDL().as_urlpatterns()
urlpatterns += ChannelLogCRUDL().as_urlpatterns()


