# This is a dummy migration which will be implemented in the next release

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("contacts", "0170_squashed"),
        ("channels", "0155_squashed"),
        ("ivr", "0021_convert_connections"),
    ]

    operations = []
