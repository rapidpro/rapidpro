# This is a dummy migration which will be implemented in the next release

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("channels", "0154_delete_channelconnection"),
    ]

    operations = []
