# This is a dummy migration which will be implemented in the next release

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("channels", "0181_squashed"),
        ("orgs", "0133_squashed"),
        ("templates", "0019_templatetranslation_templatetranslations_by_ext"),
    ]

    operations = []
