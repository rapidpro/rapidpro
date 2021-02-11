from django.conf.urls import url

from .views import (
    LoginView,
    OrgCRUDL,
    StripeHandler,
    TopUpCRUDL,
    TwoFactorBackupView,
    TwoFactorVerifyView,
    UserCRUDL,
    check_login,
)

urlpatterns = OrgCRUDL().as_urlpatterns()
urlpatterns += TopUpCRUDL().as_urlpatterns()
urlpatterns += UserCRUDL().as_urlpatterns()

urlpatterns += [
    url(r"^login/$", check_login, name="users.user_check_login"),
    url(r"^users/login/$", LoginView.as_view(), name="users.login"),
    url(r"^users/two-factor/verify/$", TwoFactorVerifyView.as_view(), name="users.two_factor_verify"),
    url(r"^users/two-factor/backup/$", TwoFactorBackupView.as_view(), name="users.two_factor_backup"),
    url(r"^handlers/stripe/$", StripeHandler.as_view(), name="handlers.stripe_handler"),
    # for backwards compatibility
    url(r"^api/v1/stripe/$", StripeHandler.as_view()),
]
