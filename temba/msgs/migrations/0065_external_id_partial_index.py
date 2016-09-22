# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0064_auto_20160908_1340'),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE INDEX msgs_msg_external_id_where_nonnull ON msgs_msg(external_id) WHERE external_id IS NOT NULL",
            "DROP INDEX msgs_msg_external_id_where_nonnull"
        )
    ]
