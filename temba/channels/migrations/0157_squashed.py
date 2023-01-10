# This is a dummy migration which will be implemented in the next release

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("contacts", "0170_squashed"),
        ("channels", "0156_squashed"),
        ("msgs", "0205_squashed"),
    ]

    operations = []
