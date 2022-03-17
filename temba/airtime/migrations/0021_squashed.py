# This is a dummy migration which will be implemented in 7.3

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0093_squashed"),
        ("airtime", "0020_squashed"),
    ]

    operations = []
