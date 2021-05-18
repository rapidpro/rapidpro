# Generated by Django 2.2.4 on 2019-08-29 23:00

from django.db import migrations

from temba import mailroom


def migrate_translations(translations):  # pragma: no cover
    return {lang: mailroom.get_client().expression_migrate(s) for lang, s in translations.items()}


def migrate_msg_events(apps, schema_editor):  # pragma: no cover
    CampaignEvent = apps.get_model("campaigns", "CampaignEvent")

    for evt in CampaignEvent.objects.all():
        if evt.message:
            evt.message = migrate_translations(evt.message)
            evt.save(update_fields=("message",))


def reverse(apps, schema_editor):  # pragma: no cover
    pass


class Migration(migrations.Migration):

    dependencies = [("campaigns", "0031_cleanup")]

    operations = [migrations.RunPython(migrate_msg_events, reverse)]
