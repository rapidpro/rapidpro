# Generated by Django 4.0.7 on 2022-09-21 15:44

from django.db import migrations, transaction


def convert_connections(apps, schema_editor):  # pragma: no cover
    ChannelConnection = apps.get_model("channels", "ChannelConnection")
    ChannelLog = apps.get_model("channels", "ChannelLog")
    FlowStart = apps.get_model("flows", "FlowStart")
    FlowSession = apps.get_model("flows", "FlowSession")
    Call = apps.get_model("ivr", "Call")

    num_converted = 0
    while True:
        batch = list(ChannelConnection.objects.all()[:1000])
        if not batch:
            break

        calls = []
        for conn in batch:
            calls.append(
                Call(
                    org_id=conn.org_id,
                    direction=conn.direction,
                    status=conn.status,
                    channel_id=conn.channel_id,
                    contact_id=conn.contact_id,
                    contact_urn_id=conn.contact_urn_id,
                    external_id=conn.external_id,
                    created_on=conn.created_on,
                    modified_on=conn.modified_on,
                    started_on=conn.started_on,
                    ended_on=conn.ended_on,
                    duration=conn.duration,
                    error_reason=conn.error_reason,
                    error_count=conn.error_count,
                    next_attempt=conn.next_attempt,
                )
            )

        with transaction.atomic():
            Call.objects.bulk_create(calls)
            ChannelLog.objects.filter(connection__in=batch).update(connection=None)
            FlowSession.objects.filter(connection__in=batch).update(connection=None)
            FlowStart.connections.through.objects.filter(channelconnection__in=batch).delete()
            ChannelConnection.objects.filter(id__in=[c.id for c in batch]).delete()

        num_converted += len(batch)
        print(f"Converted {num_converted} channel connections to calls")


def reverse(apps, schema_editor):  # pragma: no cover
    pass


def apply_manual():  # pragma: no cover
    from django.apps import apps

    convert_connections(apps, None)


class Migration(migrations.Migration):

    dependencies = [
        ("ivr", "0020_add_call"),
    ]

    operations = [migrations.RunPython(convert_connections, reverse)]
