from ...models import ClassifierType
from .views import ConnectView

class LuisType(ClassifierType):
    """
    Type for classifiers from Luis.ai
    """
    name = "Luis.ai"
    slug = "luis"
    icon = "icon-channel-external"

    connect_view = ConnectView
    connect_blurb = """
    Connect your Luis.ai app to classify messages in your flows.
    """

    def get_current_intents(self):
        """
        Should return current set of available intents for the classifier
        """
        raise NotImplementedError("model types must implement get_intents")