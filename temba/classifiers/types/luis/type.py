import requests

from django.utils import timezone

from temba.request_logs.models import HTTPLog

from ...models import ClassifierType, Intent
from .views import ConnectView


class LuisType(ClassifierType):
    """
    Type for classifiers from Luis.ai
    """

    name = "LUIS"
    slug = "luis"
    icon = "icon-luis"

    CONFIG_APP_ID = "app_id"
    CONFIG_VERSION = "version"
    CONFIG_ENDPOINT_URL = "endpoint_url"
    CONFIG_PRIMARY_KEY = "primary_key"

    AUTH_HEADER = "Ocp-Apim-Subscription-Key"

    connect_view = ConnectView
    connect_blurb = """
    <a href="https://luis.ai">LUIS</a> is a Microsoft Azure platform that lets you interpret natural language in
    your bots. It supports 13 languages and is a highly scalable paid offering.
    """

    form_blurb = """
    You can find the attributes for your app on your Luis.ai app page.
    """

    def get_active_intents_from_api(self, classifier):
        """
        Gets the current intents defined by this app, in LUIS that's an attribute of the app version
        """
        app_id = classifier.config[self.CONFIG_APP_ID]
        version = classifier.config[self.CONFIG_VERSION]
        endpoint_url = classifier.config[self.CONFIG_ENDPOINT_URL]
        primary_key = classifier.config[self.CONFIG_PRIMARY_KEY]

        start = timezone.now()
        url = endpoint_url + "/apps/" + app_id + "/versions/" + version + "/intents"
        try:
            response = requests.get(url, headers={self.AUTH_HEADER: primary_key})
            elapsed = (timezone.now() - start).total_seconds() * 1000

            response.raise_for_status()

            HTTPLog.create_from_response(
                HTTPLog.INTENTS_SYNCED, url, response, classifier=classifier, request_time=elapsed
            )

            response_json = response.json()
        except requests.RequestException as e:
            HTTPLog.create_from_exception(HTTPLog.INTENTS_SYNCED, url, e, start, classifier=classifier)
            return []

        intents = []
        for intent in response_json:
            intents.append(Intent(name=intent["name"], external_id=intent["id"]))

        return intents
