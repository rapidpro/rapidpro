# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six

from django.utils.module_loading import import_string


@six.python_2_unicode_compatible
class MessageHandler(object):  # pragma: no cover
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
        return import_string(kls)

    def __str__(self):  # pragma: no cover
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
