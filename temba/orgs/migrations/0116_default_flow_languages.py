# Generated by Django 4.0.8 on 2023-01-03 16:33

from django.db import migrations


def default_flow_languages(apps, schema_editor):
    Org = apps.get_model("orgs", "Org")

    num_updated = 0
    for org in Org.objects.filter(flow_languages__len=0):
        org.flow_languages = ["eng"]
        org.save(update_fields=("flow_languages",))
        num_updated += 1

    print(f"Updated {num_updated} orgs with no flow languages")


def reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0115_alter_org_plan"),
    ]

    operations = [migrations.RunPython(default_flow_languages, reverse)]
