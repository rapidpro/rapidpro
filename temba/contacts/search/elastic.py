from elasticsearch import Elasticsearch
from elasticsearch_dsl import Search as es_Search

from django.conf import settings

ES = Elasticsearch(hosts=[settings.ELASTICSEARCH_URL])


def get_last_modified():
    """
    Gets the last modified contact if there are any contacts
    """
    results = (
        es_Search(index="contacts")
        .params(size=1)
        .sort("-modified_on_mu")
        .source(include=["modified_on", "id"])
        .using(ES)
        .execute()
    )
    hits = results["hits"]["hits"]
    return hits[0]["_source"] if hits else None
