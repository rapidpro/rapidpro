from elasticsearch import Elasticsearch
from elasticsearch_dsl import Search as es_Search

from django.conf import settings

ES = Elasticsearch(hosts=[settings.ELASTICSEARCH_URL])


class ModelESSearch(es_Search):
    """
    * add Django model information to the elasticserach_dsl Search class
    """

    is_none = False

    def __init__(self, **kwargs):
        self.model = kwargs.pop("model", None)

        super().__init__(**kwargs)

    def _clone(self):
        new_search = super()._clone()

        # copy extra attributes
        new_search.model = self.model

        return new_search
