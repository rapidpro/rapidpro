import os
import csv

from django.utils import timezone
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from temba.orgs.models import Org
from temba.contacts.models import Contact, URN, ContactURN, ContactField, ContactGroup


class Command(BaseCommand):  # pragma: no cover
    help = "Insert contacts in bulk"

    def add_arguments(self, parser):
        parser.add_argument("org_id")
        parser.add_argument("csv_file_path")

    def handle(self, *args, **options):
        start = timezone.now()

        org = Org.objects.filter(id=int(options["org_id"])).first()
        default_user = User.objects.filter().order_by("id").first()

        filepath = options["csv_file_path"]

        if not org:
            print("Org not found")
            return

        if not os.path.exists(filepath):
            print("File not found")
            return

        fields_map = {}
        batch_size = 500
        batch_counter = 0
        contact_objs = []
        urn_objs = []
        urns_map = {}

        with open(filepath, newline="") as csvfile:
            spamreader = csv.reader(csvfile, delimiter=",", quotechar="|")

            load_len = len(list(spamreader))

            csvfile.seek(0)

            contact_group = ContactGroup.create_static(org=org, user=default_user, name=str(filepath).split("/")[-1])

            for n, row in enumerate(spamreader):
                if n == 0:
                    Contact.validate_org_import_header(row, org)

                    counter = 0
                    for header in row:
                        fields_map[counter] = header.strip()
                        counter += 1

                else:
                    contact_obj = Contact(org=org, modified_by=default_user, created_by=default_user)
                    add_contact = True

                    for key in list(fields_map.keys()):
                        field_value = row[key]
                        field_name = fields_map[key]

                        if field_name == "Name":
                            contact_obj.name = field_value

                        elif field_name == "Language":
                            contact_obj.language = field_value

                        elif str(field_name).lower().startswith("field:"):
                            custom_field_label = str(field_name).split(":")[-1]
                            contact_field = ContactField.get_or_create(
                                org=org, user=default_user, key=ContactField.make_key(custom_field_label)
                            )
                            if contact_obj.fields is None:
                                contact_obj.fields = {}

                            contact_obj.fields.update({f"{str(contact_field.uuid)}": {"text": field_value}})

                        elif str(field_name).lower().startswith("urn:"):
                            scheme = str(field_name).lower().split(":")[-1]
                            identity = URN.normalize(f"{scheme}:{field_value}")

                            existing_urn = ContactURN.lookup(org, identity)

                            if existing_urn:
                                add_contact = False
                                continue

                            urn_obj = dict(identity=identity, path=identity.split(":")[-1], scheme=scheme, org=org)
                            urns_map[contact_obj.uuid] = urn_obj

                    if add_contact:
                        contact_objs.append(contact_obj)

                batch_counter += 1

                if batch_counter >= batch_size:
                    new_contact_objs = Contact.objects.bulk_create(contact_objs)

                    for new_contact_created in new_contact_objs:
                        contact_urn = ContactURN(**urns_map[new_contact_created.uuid])
                        contact_urn.contact = new_contact_created
                        urn_objs.append(contact_urn)

                    ContactURN.objects.bulk_create(urn_objs)

                    contact_group.update_contacts(user=default_user, contacts=new_contact_objs, add=True)

                    contact_objs = []
                    urn_objs = []
                    batch_counter = 0

                    load_len -= batch_size

                    print(f">>> Remaining: {load_len}")

            if contact_objs:
                new_contact_objs_remaining = Contact.objects.bulk_create(contact_objs)

                for new_contact_created in new_contact_objs_remaining:
                    contact_urn = ContactURN(**urns_map[new_contact_created.uuid])
                    contact_urn.contact = new_contact_created
                    urn_objs.append(contact_urn)

                ContactURN.objects.bulk_create(urn_objs)

                contact_group.update_contacts(user=default_user, contacts=new_contact_objs_remaining, add=True)

                print(f">>> Remaining: {load_len - len(contact_objs)}")

        end = timezone.now()

        print(f"Started: {start}")
        print(f"Finished: {end}")
