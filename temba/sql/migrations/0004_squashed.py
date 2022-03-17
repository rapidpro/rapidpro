# This is a dummy migration which will be implemented in 7.3

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("sql", "0003_bigint_m2ms"),
    ]

    operations = []
