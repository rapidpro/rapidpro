# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from mock import patch
from temba.tests import TembaTest, MockResponse
from .models import NluApiConsumer, NLU_BOTHUB_TAG, NLU_WIT_AI_TAG

import six


class NluTest(TembaTest):
    def test_nlu_api_bothub_consumer(self):
        consumer = NluApiConsumer.factory(NLU_BOTHUB_TAG, 'BOT_KEY_STRING')
        self.assertEquals(six.text_type(consumer), 'BotHub Consumer')
        self.assertEquals(consumer.get_headers(), {'Authorization': 'Bearer BOT_KEY_STRING'}) # mocka retorno da api e valores e fazer as chamadas das funções

        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(200, """
            {
                "bot_uuid": "e5bf3007-2629-44e3-8cbe-4505ecb130e2",
                "answer": {
                    "text": "I am looking for a Mexican restaurant in the center of town",
                    "entities": [
                        {
                            "start": 19,
                            "value": "Mexican",
                            "end": 26,
                            "entity": "cuisine",
                            "extractor": "ner_crf"
                        },
                        {
                            "start": 45,
                            "value": "center",
                            "end": 51,
                            "entity": "location",
                            "extractor": "ner_crf"
                        }
                    ],
                    "intent_ranking": [
                        {
                            "confidence": 0.731929302865667,
                            "name": "restaurant_search"
                        },
                        {
                            "confidence": 0.14645046976303883,
                            "name": "goodbye"
                        },
                        {
                            "confidence": 0.07863577626166107,
                            "name": "greet"
                        },
                        {
                            "confidence": 0.04298445110963322,
                            "name": "affirm"
                        }
                    ],
                    "intent": {
                        "confidence": 0.731929302865667,
                        "name": "restaurant_search"
                    }
                }
            }
            """)
            intent, accurancy, entities = consumer.predict("I am looking for a Mexican restaurant in the center of town",
                                                           "e5bf3007-2629-44e3-8cbe-4505ecb130e2")
            self.assertEquals(intent, 'restaurant_search')
            self.assertEquals(accurancy, 0.731929302865667)
            self.assertEquals(type(entities), dict)
            self.assertEquals(entities.get('cuisine'), 'Mexican')
            self.assertEquals(entities.get('location'), 'center')

    def test_nlu_api_wit_consumer(self):
        consumer = NluApiConsumer.factory(NLU_WIT_AI_TAG, None)
        self.assertEquals(six.text_type(consumer), 'Wit.AI Consumer')
