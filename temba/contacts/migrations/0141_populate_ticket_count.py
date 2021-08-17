# Generated by Django 2.2.20 on 2021-07-28 22:03

from django.db import migrations
from django.utils import timezone

STATUS_OPEN = "O"


def populate_ticket_count(apps, schema_editor):
    Contact = apps.get_model("contacts", "Contact")

    num_updated = 0
    for contact in Contact.objects.exclude(tickets=None):
        contact.ticket_count = contact.tickets.filter(status=STATUS_OPEN).count()
        contact.modified_on = timezone.now()
        contact.save(update_fields=("ticket_count", "modified_on"))
        num_updated += 1

    if num_updated:
        print(f"Updated ticket_count for {num_updated} contacts")


def reverse(apps, schema_editor):
    pass


def apply_manual():  # pragma: no cover
    from django.apps import apps

    populate_ticket_count(apps, None)


class Migration(migrations.Migration):

    dependencies = [("contacts", "0140_zeroize_ticket_count"), ("tickets", "0017_update_trigger")]

    operations = [migrations.RunPython(populate_ticket_count, reverse)]