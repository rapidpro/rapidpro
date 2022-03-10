# This is a dummy migration which will be implemented in 7.3

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0093_squashed"),
        ("campaigns", "0044_squashed"),
    ]

    operations = []
