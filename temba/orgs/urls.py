from django.conf.urls import url

from .views import OrgCRUDL, StripeHandler, TopUpCRUDL, UserCRUDL, UserSettingsCRUDL, check_login

urlpatterns = OrgCRUDL().as_urlpatterns()
urlpatterns += UserSettingsCRUDL().as_urlpatterns()
urlpatterns += TopUpCRUDL().as_urlpatterns()
urlpatterns += UserCRUDL().as_urlpatterns()

urlpatterns += [
    url(r"^login/$", check_login, name="users.user_check_login"),
    url(r"^handlers/stripe/$", StripeHandler.as_view(), name="handlers.stripe_handler"),
    # for backwards compatibility
    url(r"^api/v1/stripe/$", StripeHandler.as_view()),
]
