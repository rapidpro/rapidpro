from elasticsearch import Elasticsearch
from elasticsearch_dsl import Search as es_Search

from django.conf import settings

from .mailroom import parse_query

ES = Elasticsearch(hosts=[settings.ELASTICSEARCH_URL])


def query_contact_ids_from_elasticsearch(org, elastic_query):
    """
    Returns the contact ids for the given elasticsearch query configuration
    """
    results = es_Search(index="contacts").source(include=["id"]).params(routing=org.id).using(ES).query(elastic_query)
    return [int(r.id) for r in results.scan()]


def query_contact_ids(org, query, *, group=None, return_parsed_query=False, active_only=True):
    """
    Returns the contact ids for the given query
    """
    parsed = parse_query(org, query, group=group)

    if not active_only:
        try:
            # remove two conditions which selects only active contacts
            parsed.elastic_query["bool"]["must"].pop(1)
            parsed.elastic_query["bool"]["must"].pop(1)
        except (IndexError, KeyError):
            pass

    # In case if you also need to return parsed query (e.g. to display it to users)
    # you just need to pass `return_parsed_query` in kwargs as `True`
    if return_parsed_query:
        return query_contact_ids_from_elasticsearch(org, parsed.elastic_query), parsed.query
    return query_contact_ids_from_elasticsearch(org, parsed.elastic_query)


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
