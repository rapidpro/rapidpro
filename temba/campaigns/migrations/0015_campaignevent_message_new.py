import json

from django.contrib.postgres.operations import HStoreExtension
from django.db import migrations

import temba.utils.models


def populate_message_new(apps, schema_editor):
    CampaignEvent = apps.get_model("campaigns", "CampaignEvent")
    events = list(CampaignEvent.objects.filter(event_type="M").select_related("flow"))

    for event in events:
        base_lang = event.flow.base_language or "base"
        try:
            msg = json.loads(event.message)
            if isinstance(msg, dict):
                event.message_new = msg
            else:
                event.message_new = {base_lang: event.message}
        except Exception:
            event.message_new = {base_lang: event.message}

        event.save(update_fields=("message_new",))

    if events:
        print("Converted %d campaign events" % len(events))


class Migration(migrations.Migration):

    atomic = False

    dependencies = [("campaigns", "0014_auto_20170228_0837")]

    operations = [
        HStoreExtension(),
        migrations.AddField(
            model_name="campaignevent",
            name="message_new",
            field=temba.utils.models.TranslatableField(max_length=640, null=True),
        ),
        migrations.RunPython(populate_message_new),
    ]
