# -*- coding: utf-8 -*-
from .views import CampaignCRUDL, CampaignEventCRUDL

urlpatterns = CampaignCRUDL().as_urlpatterns()
urlpatterns += CampaignEventCRUDL().as_urlpatterns()
