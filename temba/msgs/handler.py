from __future__ import unicode_literals


class MessageHandler(object):
    """
    Base class for message handlers.
    """
    def __init__(self, name):
        self.name = name

    @classmethod
    def find(cls, kls):
        """
        Finds the message handler from the fully qualified name that is passed in
        """
        from smartmin import class_from_string
        return class_from_string(kls)

    def __unicode__(self):  # pragma: no cover
        return self.name

    # incoming phases
    def pre_receive(self, msg):
        pass

    def receive(self, msg):
        pass

    def post_receive(self, msg):
        pass

    # main phase
    def handle(self, msg):
        pass

    # outgoing phases:
    def pre_send(self, msg):
        pass

    def send(self, msg):
        pass

    def post_send(self, msg):
        pass
