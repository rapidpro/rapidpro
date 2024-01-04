# This is a dummy migration which will be implemented in the next release

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("channels", "0180_squashed"),
        ("msgs", "0253_squashed"),
    ]

    operations = []
