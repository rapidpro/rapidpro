import requests

from temba.request_logs.models import HTTPLog

from ...models import ClassifierType, Intent
from .client import AuthoringClient
from .views import ConnectView


class LuisType(ClassifierType):
    """
    Type for classifiers from Luis.ai
    """

    name = "LUIS"
    slug = "luis"
    icon = "icon-luis"

    CONFIG_APP_ID = "app_id"
    CONFIG_AUTHORING_ENDPOINT = "authoring_endpoint"
    CONFIG_AUTHORING_KEY = "authoring_key"
    CONFIG_PREDICTION_ENDPOINT = "prediction_endpoint"
    CONFIG_PREDICTION_KEY = "prediction_key"
    CONFIG_SLOT = "slot"

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
        authoring_endpoint = classifier.config[self.CONFIG_AUTHORING_ENDPOINT]
        authoring_key = classifier.config[self.CONFIG_AUTHORING_KEY]
        slot = classifier.config[self.CONFIG_SLOT]

        client = AuthoringClient(authoring_endpoint, authoring_key)
        intents = []

        try:
            app_info = client.get_app(app_id)
            if slot.upper() in app_info["endpoints"]:
                version = app_info["endpoints"][slot.upper()]["versionId"]
                intents = client.get_version_intents(app_id, version)
        except requests.RequestException:
            pass

        for log in client.logs:
            HTTPLog.create_from_response(
                HTTPLog.INTENTS_SYNCED, log["url"], log["response"], classifier=classifier, request_time=log["elapsed"]
            )

        return [Intent(name=i["name"], external_id=i["id"]) for i in intents]
