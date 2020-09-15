from .views import ContactCRUDL, ContactFieldCRUDL, ContactGroupCRUDL, ContactImportCRUDL

urlpatterns = ContactCRUDL().as_urlpatterns()
urlpatterns += ContactGroupCRUDL().as_urlpatterns()
urlpatterns += ContactFieldCRUDL().as_urlpatterns()
urlpatterns += ContactImportCRUDL().as_urlpatterns()
