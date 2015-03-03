from __future__ import absolute_import, unicode_literals

from django.conf.urls import patterns, url
from .views import OrgCRUDL, UserSettingsCRUDL, TopUpCRUDL, UserCRUDL, check_login

urlpatterns = OrgCRUDL().as_urlpatterns()
urlpatterns += UserSettingsCRUDL().as_urlpatterns()
urlpatterns += TopUpCRUDL().as_urlpatterns()
urlpatterns += UserCRUDL().as_urlpatterns()
urlpatterns += patterns('', url(r'^login/$', check_login, name='users.user_check_login'))
