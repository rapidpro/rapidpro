from django.core.management import BaseCommand
from elasticsearch.helpers import bulk

from temba.contacts.search import elastic
from temba.contacts.models import Contact
from temba.orgs.models import Org

raw_query = """
            SELECT id,
                (
                    SELECT array_to_json(array_agg(row_to_json(u)))
                    FROM (
                        SELECT scheme,
                            path,
                            channels_channel.uuid as channel_uuid,
                            channels_channel.name as channel_name
                        FROM contacts_contacturn
                        LEFT JOIN channels_channel ON contacts_contacturn.channel_id = channels_channel.id
                        WHERE contact_id = contacts_contact.id
                    ) u
                ) as urns_channels,
                (
                    SELECT array_to_json(array_agg(row_to_json(f)))
                    FROM (
                        SELECT DISTINCT ff.uuid, ff.name
                        FROM flows_flowrun fr
                        INNER JOIN flows_flow ff on ff.id = fr.flow_id
                        WHERE fr.contact_id = contacts_contact.id
                    ) f
               ) as flows
            FROM contacts_contact
            WHERE contacts_contact.is_active = true AND contacts_contact.org_id = %d AND contacts_contact.id > %d
            ORDER BY contacts_contact.id
            LIMIT 500000;
            """


class Command(BaseCommand):
    def handle(self, *args, **options):

        org_ids = Org.objects.filter(is_active=True).order_by("id").values_list("id", flat=True)

        for org_id in org_ids:
            self.stdout.write(f"ES Contacts update with channels and flows for org={org_id}:", self.style.SUCCESS)
            batch_no = 0
            last_processed_contact_id = 0
            contacts = Contact.objects.raw(raw_query % (org_id, last_processed_contact_id))
            while contacts:
                update_actions = []
                for contact in contacts:
                    update_actions.append(
                        {
                            "_op_type": "update",
                            "_index": "contacts",
                            "_type": "_doc",
                            "_id": contact.id,
                            "_routing": org_id,
                            "doc": {
                                "urns": contact.urns_channels,
                                "flows": contact.flows,
                            },
                        }
                    )
                    last_processed_contact_id = contact.id

                batch_no += 1
                result = bulk(
                    elastic.ES, update_actions, stats_only=True, raise_on_error=False, raise_on_exception=False
                )
                self.stdout.write(
                    "Batch #{0}: ".format(batch_no) +
                    self.style.WARNING(f"{result[0]} Success ") +
                    self.style.ERROR(f"{result[1]} Error ")
                )
                contacts = Contact.objects.raw(raw_query % (org_id, last_processed_contact_id))
            self.stdout.write("\n")
