import iso8601
import requests

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from temba.contacts.models import Contact, ContactGroup
from temba.orgs.models import Org
from temba.utils import json


class Command(BaseCommand):  # pragma: no cover
    help = "Compares Elasticsearch and the DB"

    def add_arguments(self, parser):
        parser.add_argument("--org", type=str, action="store", dest="org_id", help="ID of org to check")
        parser.add_argument("--group", type=str, action="store", dest="group_uuid", help="UUID of group to check")
        parser.add_argument(
            "--contact", type=str, action="store", dest="contact_uuid", help="UUID of contact to check"
        )

    def handle(self, org_id: int, group_uuid: str, contact_uuid: str, *args, **kwargs):
        self.es = ElasticClient(settings.ELASTICSEARCH_URL)

        if org_id:
            self.compare_org(org_id)
        elif group_uuid:
            self.compare_group(group_uuid)
        elif contact_uuid:
            self.compare_contact(contact_uuid)
        else:
            raise CommandError("Must specify --org or --group or --contact")

    def compare_org(self, org_id: int):
        org = Org.objects.filter(id=org_id, is_active=True).first()
        if not org:
            raise CommandError("No such org")

        for group in org.all_groups.filter(is_active=True):
            db_count = group.get_member_count()
            es_count = self.es.count(
                "contacts", {"query": {"bool": {"filter": [{"term": {"groups": str(group.uuid)}}]}}}
            )

            if db_count != es_count:
                self.stdout.write(
                    f"Group count mismatch uuid={group.uuid} db_count={db_count} es_count={es_count} name={group.name}"
                )

    def compare_group(self, group_uuid: str):
        group = ContactGroup.all_groups.filter(uuid=group_uuid, is_active=True).first()
        if not group:
            raise CommandError("No such group")

        # get the contacts in ES and in the DB organized into dicts by UUID
        es_contacts = self.get_es_group_contacts(group)
        db_contacts = {str(c.uuid): c for c in group.contacts.filter(is_active=True)}

        self.stdout.write(f"DB count: {len(db_contacts)} ES Count: {len(es_contacts)}")

        for uuid, db_contact in db_contacts.items():
            if uuid not in es_contacts:
                es_contact = self.get_es_contact(uuid)
                db_mod_on = db_contact.modified_on.isoformat()
                es_mod_on = iso8601.parse_date(es_contact["modified_on"]).isoformat()
                self.stdout.write(
                    f"Extra DB uuid={uuid} db_modified_on={db_mod_on} es_modified_on={es_mod_on} name={db_contact.name}"
                )

        for uuid, es_contact in es_contacts.items():
            if uuid not in db_contacts:
                db_contact = Contact.objects.get(uuid=uuid)
                db_mod_on = db_contact.modified_on.isoformat()
                es_mod_on = iso8601.parse_date(es_contact["modified_on"]).isoformat()
                self.stdout.write(
                    f"Extra ES uuid={uuid} db_modified_on={db_mod_on} es_modified_on={es_mod_on} name={es_contact['name']}"
                )

    def compare_contact(self, contact_uuid: str):
        db_contact = Contact.objects.filter(uuid=contact_uuid, is_active=True).first()
        if not db_contact:
            raise CommandError("No such contact")

        as_json = {
            "id": db_contact.id,
            "name": db_contact.name,
            "language": db_contact.language,
            "status": db_contact.status,
            "tickets": db_contact.ticket_count,
            "is_active": db_contact.is_active,
            "created_on": db_contact.created_on,
            "modified_on": db_contact.modified_on,
            "last_seen_on": db_contact.last_seen_on,
            "urns": [{"scheme": u.scheme, "path": u.path} for u in db_contact.urns.all()],
            "fields": db_contact.fields,
            "groups": [str(g.uuid) for g in db_contact.all_groups.all()],
        }

        self.stdout.write("========================= DB =========================")
        self.stdout.write(json.dumps(as_json, indent=2))

        es_contact = self.get_es_contact(contact_uuid)
        self.stdout.write("========================= ES =========================")
        self.stdout.write(json.dumps(es_contact, indent=2))

    def get_es_group_contacts(self, group) -> dict:
        search = {
            "_source": ["uuid", "name", "modified_on"],
            "from": 0,
            "size": 10000,
            "query": {"bool": {"filter": [{"term": {"groups": str(group.uuid)}}]}},
            "sort": [{"modified_on_mu": {"order": "desc"}}],
        }
        return {hit["_source"]["uuid"]: hit["_source"] for hit in self.es.search("contacts", search)}

    def get_es_contact(self, uuid):
        search = {
            "from": 0,
            "size": 1,
            "query": {"bool": {"filter": [{"term": {"uuid": uuid}}]}},
            "sort": [{"modified_on_mu": {"order": "desc"}}],
        }

        hits = self.es.search("contacts", search)
        if not len(hits) == 1:
            raise CommandError(f"ES search for contact with UUID returned {len(hits)} results")

        return hits[0]["_source"]


class ElasticClient:
    def __init__(self, url: str):
        self.url = url

    def search(self, index: str, search: dict) -> list:
        response = requests.get(f"{self.url}/{index}/_search", json=search).json()
        if "hits" not in response or "hits" not in response["hits"]:
            raise ValueError(json.dumps(response))
        return response["hits"]["hits"]

    def count(self, index: str, search: dict) -> int:
        response = requests.get(f"{self.url}/{index}/_count", json=search).json()
        if "count" not in response:
            raise ValueError(json.dumps(response))
        return response["count"]
