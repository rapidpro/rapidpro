from .views import *

urlpatterns = OrgCRUDL().as_urlpatterns()
urlpatterns += UserSettingsCRUDL().as_urlpatterns()
urlpatterns += TopUpCRUDL().as_urlpatterns()
urlpatterns += UserCRUDL().as_urlpatterns()
urlpatterns += patterns('', url(r'^login/$', check_login, name='users.user_check_login'))
