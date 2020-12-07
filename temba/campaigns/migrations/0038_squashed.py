# This is a dummy migration which will be implemented in 6.1

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0072_squashed"),
        ("campaigns", "0037_squashed"),
    ]

    operations = []
