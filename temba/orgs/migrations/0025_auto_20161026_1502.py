# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import timezone_field.fields


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0024_remove_invitation_host'),
    ]

    operations = [
        migrations.AlterField(
            model_name='org',
            name='timezone',
            field=timezone_field.fields.TimeZoneField(verbose_name='Timezone'),
        ),
    ]
