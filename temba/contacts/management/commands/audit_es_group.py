import os

import requests

from django.conf import settings
from django.core.management.base import BaseCommand

from temba.contacts.models import ContactGroup


class Command(BaseCommand):  # pragma: no cover
    help = "Checks group membership between Elasticsearch and the DB for the passed in group uuid"

    def add_arguments(self, parser):
        parser.add_argument("group_uuid")

    def handle(self, *args, **options):
        group = ContactGroup.all_groups.get(uuid=options["group_uuid"])

        search = {
            "_source": ["modified_on", "created_on", "uuid", "name"],
            "from": 0,
            "size": 10000,
            "query": {"bool": {"filter": [{"term": {"groups": options["group_uuid"]}}]}},
            "sort": [{"modified_on_mu": {"order": "desc"}}],
        }

        es_response = requests.get(settings.ELASTICSEARCH_URL + "/contacts/_search", json=search).json()
        if "hits" not in es_response:
            print(es_response)
            os.exit(1)

        es_contacts = es_response["hits"]["hits"]
        db_contacts = group.contacts.filter(is_active=True)

        es_map = {}
        for hit in es_contacts:
            es_map[hit["_source"]["uuid"]] = hit

        print("DB count: %d ES Count: %d" % (db_contacts.count(), len(es_contacts)))

        for contact in db_contacts:
            db_uuid = str(contact.uuid)
            if db_uuid not in es_map:
                print("Extra DB hit:", db_uuid, contact.created_on, contact.modified_on, contact.name)
            else:
                del es_map[db_uuid]

        for hit in es_map.values():
            c = hit["_source"]
            print("Extra ES hit:", c["uuid"], c["created_on"], c["modified_on"], c["name"])
