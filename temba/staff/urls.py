from .views import OrgCRUDL, UserCRUDL

urlpatterns = OrgCRUDL().as_urlpatterns()
urlpatterns += UserCRUDL().as_urlpatterns()
