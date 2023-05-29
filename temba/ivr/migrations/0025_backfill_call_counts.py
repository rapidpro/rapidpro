# Generated by Django 4.1.9 on 2023-05-29 20:59

from django.db import migrations, transaction


def backfill_call_counts(apps, schema_editor):
    Org = apps.get_model("orgs", "Org")
    Call = apps.get_model("ivr", "Call")
    SystemLabelCount = apps.get_model("msgs", "SystemLabelCount")

    org_ids_with_calls = list(Call.objects.values_list("org", flat=True).distinct())

    for org in Org.objects.filter(id__in=org_ids_with_calls):
        with transaction.atomic():
            org.system_labels.filter(label_type="C").delete()

            call_count = org.calls.count()

            SystemLabelCount.objects.create(org=org, label_type="C", count=call_count, is_squashed=True)

        print(f"Backfilled call count for org '{org.name}' (calls={call_count})")


def reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("ivr", "0024_count_db_triggers"),
    ]

    operations = [migrations.RunPython(backfill_call_counts, reverse)]
