# This is a dummy migration which will be implemented in 6.1

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0124_squashed"),
        ("orgs", "0072_squashed"),
        ("templates", "0006_templatetranslation_country"),
    ]

    operations = []
