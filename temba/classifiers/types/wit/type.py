import requests

from django.utils import timezone

from temba.request_logs.models import HTTPLog

from ...models import ClassifierType, Intent
from .client import Client
from .views import ConnectView


class WitType(ClassifierType):
    """
    Type for classifiers from Wit.ai
    """

    CONFIG_ACCESS_TOKEN = "access_token"
    CONFIG_APP_ID = "app_id"

    name = "Wit.ai"
    slug = "wit"
    icon = "icon-wit"

    connect_view = ConnectView
    connect_blurb = """
        <a href="https://wit.ai">Wit.ai</a> is a Facebook owned natural language platform that supports up to 132 languages.
        The service is free and easy to use and a great choice for small to medium sized bots.
        """

    form_blurb = """
        You can find the parameters below on your Wit.ai console under your App settings.
        """

    def get_active_intents_from_api(self, classifier):
        """
        Gets the current intents defined by this app. In Wit intents are treated as a special case of an entity. We
        fetch the possible values for that entity.
        """
        client = Client(classifier.config[self.CONFIG_ACCESS_TOKEN])
        start = timezone.now()

        try:
            intents, response = client.get_intents()
            elapsed = (timezone.now() - start).total_seconds() * 1000

            HTTPLog.create_from_response(
                HTTPLog.INTENTS_SYNCED, response.url, response, classifier=classifier, request_time=elapsed
            )

        except requests.RequestException as e:
            HTTPLog.create_from_exception(HTTPLog.INTENTS_SYNCED, e.request.url, e, start, classifier=classifier)
            return []

        return [Intent(name=i["name"], external_id=i["id"]) for i in intents]
