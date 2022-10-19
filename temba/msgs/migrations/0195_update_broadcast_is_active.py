from django.db import migrations


def update_broadcast_is_active(apps, schema_editor):  # pragma: no cover
    Broadcast = apps.get_model("msgs", "Broadcast")

    num_updated = 0
    while True:
        batch_ids = list(Broadcast.objects.filter(is_active=None).values_list("id", flat=True)[:5000])

        if not batch_ids:
            break

        Broadcast.objects.filter(id__in=batch_ids).update(is_active=True)

        num_updated += len(batch_ids)
        print(f"Updated {num_updated} broadcasts without an is_active")


def reverse(apps, schema_editor):  # pragma: no cover
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("msgs", "0194_broadcast_is_active"),
    ]

    operations = [migrations.RunPython(update_broadcast_is_active, reverse)]
