# This is a dummy migration which will be implemented in 7.3

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("globals", "0006_squashed"),
        ("orgs", "0093_squashed"),
    ]

    operations = []
