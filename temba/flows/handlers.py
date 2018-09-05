
from temba.msgs.handler import MessageHandler

from .models import Flow


class FlowHandler(MessageHandler):
    def __init__(self):
        super().__init__("rules")

    def handle(self, msg):
        # hand off to our Flow object to handle
        (handled, msgs) = Flow.find_and_handle(msg, allow_trial=True)
        return handled
