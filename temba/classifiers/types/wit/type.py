from ...models import ClassifierType, Intent, ClassifierLog
from .views import ConnectView

import requests
from django.utils.translation import ugettext_lazy as _
from django.utils import timezone
from requests_toolbelt.utils import dump

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

    INTENT_URL = "https://api.wit.ai/entities/intent"

    @classmethod
    def get_active_intents_from_api(cls, classifier, logs):
        """
        Gets the current intents defined by this app. In Wit intents are treated as a special case of an entity. We
        fetch the possible values for that entity.
        """
        access_token = classifier.config.get(cls.CONFIG_ACCESS_TOKEN)
        assert access_token is not None

        start = timezone.now()
        response = requests.get(cls.INTENT_URL, headers={"Authorization": f"Bearer {access_token}"})
        elapsed = (timezone.now() - start).total_seconds() * 1000

        log = ClassifierLog.from_response(classifier, cls.INTENT_URL, response, "Synced Intents", "Syncing Error")
        log.request_time = elapsed
        logs.append(log)

        response_json = response.json()

        intents = []
        for intent in response_json["values"]:
            intents.append(Intent(name=intent["value"], external_id=intent["value"]))

        return intents