# This is a dummy migration which will be implemented in 7.3

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0137_squashed"),
        ("ivr", "0017_squashed"),
    ]

    operations = []
