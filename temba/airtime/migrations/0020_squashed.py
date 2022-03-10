# This is a dummy migration which will be implemented in 7.3

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("contacts", "0152_squashed"),
        ("airtime", "0019_squashed"),
    ]

    operations = []
