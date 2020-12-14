import requests

from django.utils import timezone

from temba.request_logs.models import HTTPLog

from ...models import ClassifierType, Intent
from .views import ConnectView


class BothubType(ClassifierType):
    """
    Type for classifiers from Bothub
    """

    CONFIG_ACCESS_TOKEN = "access_token"

    name = "Bothub"
    slug = "bothub"
    icon = "icon-bothub"

    connect_view = ConnectView
    connect_blurb = """
        <a href="https://bothub.it">Bothub</a> is an Open Source NLP platform. It supports 29 languages ​​and is evolving to include the languages ​​and dialects of remote cultures.
        """

    form_blurb = """
        You can find the access token for your bot on the Integration tab.
        """

    INTENT_URL = "https://nlp.bothub.it/info/"

    def get_active_intents_from_api(self, classifier):
        access_token = classifier.config[self.CONFIG_ACCESS_TOKEN]

        start = timezone.now()
        try:
            response = requests.get(self.INTENT_URL, headers={"Authorization": f"Bearer {access_token}"})
            elapsed = (timezone.now() - start).total_seconds() * 1000

            response.raise_for_status()

            HTTPLog.create_from_response(
                HTTPLog.INTENTS_SYNCED, self.INTENT_URL, response, classifier=classifier, request_time=elapsed
            )

            response_json = response.json()
        except requests.RequestException as e:
            HTTPLog.create_from_exception(HTTPLog.INTENTS_SYNCED, self.INTENT_URL, e, start, classifier=classifier)
            return []

        intents = []
        for intent in response_json["intents"]:
            intents.append(Intent(name=intent, external_id=intent))

        return intents
