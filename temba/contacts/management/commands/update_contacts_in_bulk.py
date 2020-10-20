import os
import csv

from django.utils import timezone
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from temba.orgs.models import Org
from temba.contacts.models import Contact, URN, ContactField


class Command(BaseCommand):  # pragma: no cover
    help = "Update contacts in bulk"

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

        def get_urn_as_string(keys):
            _urn_string = None
            for _key in keys:
                _field_name = fields_map[_key]
                _field_value = row[_key]
                if str(_field_name).lower().startswith("urn:"):
                    _scheme = str(_field_name).lower().split(":")[-1]
                    _urn_string = URN.normalize(f"{_scheme}:{_field_value}")
                    break
            return _urn_string

        with open(filepath, newline="") as csvfile:
            spamreader = csv.reader(csvfile, delimiter=",")

            load_len = len(list(spamreader))

            csvfile.seek(0)

            for n, row in enumerate(spamreader):
                if n == 0:
                    Contact.validate_org_import_header(row, org)

                    counter = 0
                    for header in row:
                        fields_map[counter] = header.strip()
                        counter += 1

                else:
                    contact_urn = get_urn_as_string(list(fields_map.keys()))
                    existing_contact = Contact.from_urn(org=org, urn_as_string=contact_urn)

                    if not existing_contact:
                        continue

                    for key in list(fields_map.keys()):
                        field_value = row[key]
                        field_name = fields_map[key]

                        if field_name == "Name":
                            existing_contact.name = field_value

                        elif field_name == "Language":
                            existing_contact.language = field_value

                        elif str(field_name).lower().startswith("field:"):
                            custom_field_label = str(field_name).split(":")[-1]
                            contact_field = ContactField.get_or_create(
                                org=org, user=default_user, key=ContactField.make_key(custom_field_label)
                            )
                            if existing_contact.fields is None:
                                existing_contact.fields = {}

                            existing_contact.fields.update({f"{str(contact_field.uuid)}": {"text": field_value}})

                    contact_objs.append(existing_contact)

                batch_counter += 1

                if batch_counter >= batch_size:
                    Contact.objects.bulk_update(objs=contact_objs, fields=["name", "language", "fields"])

                    contact_objs = []
                    batch_counter = 0

                    load_len -= batch_size

                    print(f">>> Remaining: {load_len}")

            if contact_objs:
                Contact.objects.bulk_update(objs=contact_objs, fields=["name", "language", "fields"])

                print(f">>> Remaining: {load_len - len(contact_objs)}")

        end = timezone.now()

        print(f"Started: {start}")
        print(f"Finished: {end}")
