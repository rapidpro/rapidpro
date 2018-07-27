# -*- coding: utf-8 -*-
from .views import ContactCRUDL, ContactGroupCRUDL, ContactFieldCRUDL

urlpatterns = ContactCRUDL().as_urlpatterns()
urlpatterns += ContactGroupCRUDL().as_urlpatterns()
urlpatterns += ContactFieldCRUDL().as_urlpatterns()
