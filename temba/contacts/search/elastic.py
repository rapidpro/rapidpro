from elasticsearch import Elasticsearch
from elasticsearch_dsl import Search as es_Search

from django.conf import settings

from .mailroom import parse_query

ES = Elasticsearch(hosts=[settings.ELASTICSEARCH_URL])


class ModelESSearch(es_Search):
    """
    Adds Django model information to the elasticserach_dsl search class
    """

    is_none = False

    def __init__(self, **kwargs):
        self.model = kwargs.pop("model", None)

        super().__init__(**kwargs)

    def _clone(self):
        new_search = super()._clone()
        new_search.model = self.model  # copy extra attributes
        return new_search


def query_contact_ids(org, query, *, group=None):
    from temba.contacts.models import Contact

    parsed = parse_query(org, query, group=group)
    results = (
        ModelESSearch(model=Contact, index="contacts")
        .source(include=["id"])
        .params(routing=org.id)
        .using(ES)
        .query(parsed.elastic_query)
    )

    return [int(r.id) for r in results.scan()]


def get_last_modified():
    from temba.contacts.models import Contact

    results = (
        ModelESSearch(model=Contact, index="contacts")
        .params(size=1)
        .sort("-modified_on_mu")
        .source(include=["modified_on", "id"])
        .using(ES)
        .execute()
    )
    hits = results["hits"]["hits"]
    return hits[0]["_source"] if hits else None
