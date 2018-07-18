from django.db import migrations

from temba.values.constants import Value


def add_system_contact_fields(apps, schema_editor):
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
        created_on.save()

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
        contact_name.save()

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
        language.save()


class Migration(migrations.Migration):

    dependencies = [("contacts", "0088_auto_20180718_1530")]

    operations = [migrations.RunPython(add_system_contact_fields)]
