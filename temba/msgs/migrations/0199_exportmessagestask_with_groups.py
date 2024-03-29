# Generated by Django 4.0.7 on 2022-10-21 21:22

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contacts", "0169_alter_contact_language_alter_contact_name"),
        ("msgs", "0198_remove_exportmessagestask_groups"),
    ]

    operations = [
        migrations.AddField(
            model_name="exportmessagestask",
            name="with_groups",
            field=models.ManyToManyField(related_name="%(class)s_exports", to="contacts.contactgroup"),
        ),
    ]
