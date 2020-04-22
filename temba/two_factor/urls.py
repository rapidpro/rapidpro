from django.conf.urls import url

from .views import BackupTokenView, LoginView, TokenView

urlpatterns = [
    url(r"^users/login/$", LoginView.as_view(), name="two_factor.login"),
    url(r"^users/token/$", TokenView.as_view(), name="two_factor.token"),
    url(r"^users/backup_tokens/$", BackupTokenView.as_view(), name="two_factor.backup_tokens"),
]
