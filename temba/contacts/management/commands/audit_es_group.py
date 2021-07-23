import json

import iso8601
import requests

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from temba.contacts.models import Contact, ContactGroup


class Command(BaseCommand):  # pragma: no cover
    help = "Checks group membership between Elasticsearch and the DB for the passed in group uuid"

    def add_arguments(self, parser):
        parser.add_argument("group_uuid")

    def handle(self, group_uuid, *args, **kwargs):
        verbosity = kwargs["verbosity"]

        # get the contacts in ES and in the DB organized into dicts by UUID
        es_contacts = self.get_es_group_contacts(group_uuid)
        db_contacts = self.get_db_group_contacts(group_uuid)

        print("DB count: %d ES Count: %d" % (len(db_contacts), len(es_contacts)))

        for uuid, db_contact in db_contacts.items():
            if uuid not in es_contacts:
                es_contact = self.get_es_contact(uuid)
                db_mod_on = db_contact.modified_on.isoformat()
                es_mod_on = iso8601.parse_date(es_contact["modified_on"]).isoformat()
                print(
                    f"Extra DB uuid={uuid} db_modified_on={db_mod_on} es_modified_on={es_mod_on} name={db_contact.name}"
                )
                if verbosity >= 2:
                    print(" > " + json.dumps(es_contact))

        for uuid, es_contact in es_contacts.items():
            if uuid not in db_contacts:
                db_contact = Contact.objects.get(uuid=uuid)
                db_mod_on = db_contact.modified_on.isoformat()
                es_mod_on = iso8601.parse_date(es_contact["modified_on"]).isoformat()
                print(
                    f"Extra ES uuid={uuid} db_modified_on={db_mod_on} es_modified_on={es_mod_on} name={es_contact['name']}"
                )

    def get_db_group_contacts(self, uuid) -> dict:
        group = ContactGroup.all_groups.get(uuid=uuid)
        return {str(c.uuid): c for c in group.contacts.filter(is_active=True)}

    def get_es_group_contacts(self, uuid) -> dict:
        search = {
            "_source": ["uuid", "name", "modified_on"],
            "from": 0,
            "size": 10000,
            "query": {"bool": {"filter": [{"term": {"groups": uuid}}]}},
            "sort": [{"modified_on_mu": {"order": "desc"}}],
        }
        return {hit["_source"]["uuid"]: hit["_source"] for hit in self.es_search(search)}

    def get_es_contact(self, uuid):
        search = {
            "from": 0,
            "size": 1,
            "query": {"bool": {"filter": [{"term": {"uuid": uuid}}]}},
            "sort": [{"modified_on_mu": {"order": "desc"}}],
        }

        hits = self.es_search(search)
        if not len(hits) == 1:
            raise CommandError(f"ES search for contact with UUID returned {len(hits)} results")

        return hits[0]["_source"]

    def es_search(self, search: dict) -> list:
        response = requests.get(settings.ELASTICSEARCH_URL + "/contacts/_search", json=search).json()
        if "hits" not in response or "hits" not in response["hits"]:
            raise CommandError(json.dumps(response))

        return response["hits"]["hits"]
