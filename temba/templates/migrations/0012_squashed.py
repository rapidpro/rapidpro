# This is a dummy migration which will be implemented in 7.3

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0093_squashed"),
        ("channels", "0137_squashed"),
        ("templates", "0011_auto_20210513_1519"),
    ]

    operations = []
