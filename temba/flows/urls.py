from .views import FlowCRUDL, FlowLabelCRUDL, FlowRunCRUDL, FlowSessionCRUDL, FlowStartCRUDL

urlpatterns = FlowCRUDL().as_urlpatterns()
urlpatterns += FlowLabelCRUDL().as_urlpatterns()
urlpatterns += FlowRunCRUDL().as_urlpatterns()
urlpatterns += FlowSessionCRUDL().as_urlpatterns()
urlpatterns += FlowStartCRUDL().as_urlpatterns()
