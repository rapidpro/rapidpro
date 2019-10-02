from ...models import ClassifierType, Intent
from .views import ConnectView

import requests

class WitType(ClassifierType):
    """
    Type for classifiers from Wit.ai
    """
    CONFIG_ACCESS_TOKEN = "access_token"
    CONFIG_APP_ID = "app_id"

    name = "Wit.ai"
    slug = "wit"
    icon = "icon-channel-external"

    connect_view = ConnectView
    connect_blurb = """
    Connect your Wit.ai app to classify messages in your flow.
    """

    @classmethod
    def get_active_intents_from_api(cls, classifier):
        """
        Gets the current intents defined by this app. In Wit intents are treated as a special case of an entity. We
        fetch the possible values for that entity.
        """
        access_token = classifier.config.get(cls.CONFIG_ACCESS_TOKEN)
        assert access_token is not None

        response = requests.get("https://api.wit.ai/entities/intent",
                                headers={"Authorization": f"Bearer {access_token}"})

        response.raise_for_status()

        intents = []
        for intent in response.json()["values"]:
            intents.append(Intent(name=intent["value"], external_id=intent["value"]))

        return intents