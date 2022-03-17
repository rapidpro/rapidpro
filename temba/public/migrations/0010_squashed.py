# This is a dummy migration which will be implemented in 7.3

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("public", "0009_alter_lead_created_by_alter_lead_modified_by_and_more"),
    ]

    operations = []
