import itertools

from django.db import migrations

from temba.values.constants import Value


def contact_field_generator(apps):
    Org = apps.get_model("orgs.Org")
    ContactField = apps.get_model("contacts.ContactField")

    for org in Org.objects.all():
        created_on = ContactField(
            org_id=org.id,
            label="Created On",
            key="created_on",
            value_type=Value.TYPE_DATETIME,
            show_in_table=False,
            created_by=org.created_by,
            modified_by=org.modified_by,
            field_type="S",
        )
        yield created_on

        contact_name = ContactField(
            org_id=org.id,
            label="Contact Name",
            key="name",
            value_type=Value.TYPE_TEXT,
            show_in_table=False,
            created_by=org.created_by,
            modified_by=org.modified_by,
            field_type="S",
        )
        yield contact_name

        language = ContactField(
            org_id=org.id,
            label="Language",
            key="language",
            value_type=Value.TYPE_TEXT,
            show_in_table=False,
            created_by=org.created_by,
            modified_by=org.modified_by,
            field_type="S",
        )
        yield language


def add_system_contact_fields(apps, schema_editor):
    ContactField = apps.get_model("contacts.ContactField")
    all_contact_fields = contact_field_generator(apps)

    # https://docs.djangoproject.com/en/2.0/ref/models/querysets/#bulk-create
    batch_size = 1000
    while True:
        batch = list(itertools.islice(all_contact_fields, batch_size))

        if len(batch) == 0:
            break

        ContactField.all_fields.bulk_create(batch, batch_size)


class Migration(migrations.Migration):

    dependencies = [("contacts", "0088_auto_20180718_1530")]

    operations = [migrations.RunPython(add_system_contact_fields)]
