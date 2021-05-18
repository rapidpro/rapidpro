# This is a dummy migration which will be implemented in 6.1

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("airtime", "0015_squashed"),
        ("contacts", "0128_squashed"),
    ]

    operations = []
