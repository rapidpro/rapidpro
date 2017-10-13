# -*- coding: utf-8 -*-
from __future__ import unicode_literals


NLU_API_NAME = 'NLU_API_NAME'
NLU_API_KEY = 'NLU_API_KEY'

NLU_BOTHUB_TAG = 'BTH'
NLU_WIT_AI_TAG = 'WIT'

NLU_API_CHOICES = (
    (NLU_BOTHUB_TAG, 'BotHub'),
    (NLU_WIT_AI_TAG, 'Wit.AI'),)

NLU_API_WITHOUT_KEY = [NLU_WIT_AI_TAG]


class BaseConsumer(object):
    def __init__(self, auth, nlu_type):
        self.auth = auth
        self.name = "%s Consumer" % dict(NLU_API_CHOICES).get(nlu_type)

    def __str__(self):
        return self.name

    def list_bots(self):
        pass

    def predict(self, msg):
        pass


class BothubConsumer(BaseConsumer):
    def predict(self, msg):
        # CALL BOTHUB API
        return "BOTHUB RETURN"


class WitConsumer(BaseConsumer):
    def predict(self, msg):
        # CALL WIT API
        return "WIT RETURN"


class NluApiConsumer(object):
    @staticmethod
    def factory(nlu_type, auth):
        if nlu_type == NLU_BOTHUB_TAG:
            return BothubConsumer(auth, nlu_type)
        if nlu_type == NLU_WIT_AI_TAG:
            return WitConsumer(auth, nlu_type)
