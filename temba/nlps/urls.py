from django.conf.urls import url

from .views import ClaimNLPProviders


urlpatterns = [
    url(r"^nlpproviders/claim", ClaimNLPProviders.as_view(), name="add_nlp_provider")
]
