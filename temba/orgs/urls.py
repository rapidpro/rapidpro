# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.conf.urls import url
from .views import OrgCRUDL, UserSettingsCRUDL, TopUpCRUDL, UserCRUDL, check_login, StripeHandler

urlpatterns = OrgCRUDL().as_urlpatterns()
urlpatterns += UserSettingsCRUDL().as_urlpatterns()
urlpatterns += TopUpCRUDL().as_urlpatterns()
urlpatterns += UserCRUDL().as_urlpatterns()

urlpatterns += [
    url(r'^login/$', check_login, name='users.user_check_login'),
    url(r'^handlers/stripe/$', StripeHandler.as_view(), name='handlers.stripe_handler'),

    # for backwards compatibility
    url(r'^api/v1/stripe/$', StripeHandler.as_view())
]
