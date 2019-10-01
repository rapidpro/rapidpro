from ...models import ClassifierType
from .views import ConnectView

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

    def get_current_intents(self):
        """
        Should return current set of available intents for the classifier
        """
        raise NotImplementedError("model types must implement get_intents")