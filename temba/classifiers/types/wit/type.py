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

    @classmethod
    def get_active_intents_from_api(cls, classifier, logs):
        """
        Gets the current intents defined by this app. In Wit intents are treated as a special case of an entity. We
        fetch the possible values for that entity.
        """
        access_token = classifier.config.get(cls.CONFIG_ACCESS_TOKEN)
        assert access_token is not None

        response = requests.get("https://api.wit.ai/entities/intent",
                                headers={"Authorization": f"Bearer {access_token}"})

        is_error = response.status_code != 200
        description = _("Syncing Error") if is_error else _("Synced Intents")

        log = dump.dump_all(response).decode('utf-8')
        logs.append((
            ClassifierLog(classifier=classifier, log=log,
                          is_error=is_error, description=description, created_on=timezone.now()))
        )

        response_json = response.json()

        intents = []
        for intent in response_json["values"]:
            intents.append(Intent(name=intent["value"], external_id=intent["value"]))

        return intents