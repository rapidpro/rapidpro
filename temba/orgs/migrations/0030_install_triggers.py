from django.db import migrations

from temba.sql import InstallSQL


class Migration(migrations.Migration):

    dependencies = [("orgs", "0029_reset_1"), ("channels", "0053_reset_4"), ("msgs", "0076_install_triggers")]

    operations = [InstallSQL("0030_orgs")]
