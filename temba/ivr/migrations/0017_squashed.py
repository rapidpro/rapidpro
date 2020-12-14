# This is a dummy migration which will be implemented in 6.1

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0124_squashed"),
        ("ivr", "0016_initial"),
    ]

    operations = []
