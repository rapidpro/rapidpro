# This is a dummy migration which will be implemented in 7.3

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0009_notification_ticket_export"),
    ]

    operations = []
