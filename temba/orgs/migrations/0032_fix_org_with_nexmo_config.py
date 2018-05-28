import json
from uuid import uuid4

import nexmo as nx

from django.conf import settings
from django.core.cache import cache
from django.db import migrations
from django.urls import reverse

from temba.ivr.clients import NexmoClient
from temba.orgs.models import NEXMO_APP_ID, NEXMO_APP_PRIVATE_KEY, NEXMO_KEY, NEXMO_SECRET, NEXMO_UUID


def update_nexmo_config(Org):
    if settings.IS_PROD:
        nexmo_orgs = Org.objects.filter(config__icontains="NEXMO_KEY")

        updated_orgs = set()
        failed_orgs = set()

        for org in nexmo_orgs:
            try:
                config = json.loads(org.config) if org.config else {}
                nexmo_api_key = config.get(NEXMO_KEY, None)
                nexmo_secret = config.get(NEXMO_SECRET, None)
                nexmo_uuid = str(uuid4())

                nx_client = nx.Client(key=nexmo_api_key, secret=nexmo_secret)

                app_name = "%s/%s" % (settings.HOSTNAME.lower(), nexmo_uuid)
                answer_url = reverse("handlers.nexmo_call_handler", args=["answer", nexmo_uuid])

                event_url = reverse("handlers.nexmo_call_handler", args=["event", nexmo_uuid])

                params = dict(
                    name=app_name,
                    type="voice",
                    answer_url=answer_url,
                    answer_method="POST",
                    event_url=event_url,
                    event_method="POST",
                )

                response = nx_client.create_application(params=params)
                app_id = response.get("id", None)
                private_key = response.get("keys", dict()).get("private_key", None)

                config[NEXMO_APP_ID] = app_id
                config[NEXMO_APP_PRIVATE_KEY] = private_key
                config[NEXMO_UUID] = nexmo_uuid

                org.config = json.dumps(config)
                org.save()

                org_channels = org.channels.exclude(channel_type="A")
                # clear all our channel configurations
                for channel in org_channels:
                    key = "channel_config:%d" % channel.id
                    cache.delete(key)

                # for NX channels update the roles according to features available on Nexmo
                nexmo_client = NexmoClient(nexmo_api_key, nexmo_secret, app_id, private_key, org=org)

                org_nexmo_channels = org.channels.filter(channel_type="NX")

                for channel in org_nexmo_channels:
                    mo_path = reverse("handlers.nexmo_handler", args=["receive", nexmo_uuid])

                    nexmo_client.update_nexmo_number(
                        str(channel.country), channel.address, "https://%s%s" % (settings.HOSTNAME, mo_path), app_id
                    )

                    nexmo_phones = nexmo_client.get_numbers(channel.address)
                    features = [elt.upper() for elt in nexmo_phones[0]["features"]]
                    role = ""
                    if "SMS" in features:
                        role += "S" + "R"  # Channel.ROLE_SEND + Channel.ROLE_RECEIVE

                    if "VOICE" in features:
                        role += "A" + "C"  # Channel.ROLE_ANSWER + Channel.ROLE_CALL

                    channel.role = role
                    channel.save()

                updated_orgs.add(org.pk)
                print("Migrations successfully updated nexmo config for Org %d" % org.pk)

            except Exception as e:
                print("Migrations failed to update nexmo config for org %d with error %s" % (org.pk, str(e)))
                failed_orgs.add(org.pk)

        print(
            "Migrations finished updating nexmo config UPDATED: %d orgs , FAILED: %d orgs"
            % (len(updated_orgs), len(failed_orgs))
        )
        print("=" * 80)
        print("Updated orgs: %s" % updated_orgs)
        print("Failed orgs: %s" % failed_orgs)


def apply_as_migration(apps, schema_editor):
    Org = apps.get_model("orgs", "Org")

    update_nexmo_config(Org)


def apply_manual():
    from temba.orgs.models import Org

    update_nexmo_config(Org)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [("orgs", "0031_is_squashed")]

    operations = [migrations.RunPython(apply_as_migration, noop)]
