# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ivr', '0010_auto_20160818_2150'),
        ('flows', '0073_auto_20161111_1534'),
    ]

    database_operations = [
        migrations.AlterModelTable('IVRCall', 'channels_channelsession')
    ]

    state_operations = [
        migrations.DeleteModel('IVRCall')
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=database_operations,
            state_operations=state_operations)
    ]
