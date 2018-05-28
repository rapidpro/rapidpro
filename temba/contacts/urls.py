from .views import ContactCRUDL, ContactFieldCRUDL, ContactGroupCRUDL

urlpatterns = ContactCRUDL().as_urlpatterns()
urlpatterns += ContactGroupCRUDL().as_urlpatterns()
urlpatterns += ContactFieldCRUDL().as_urlpatterns()
