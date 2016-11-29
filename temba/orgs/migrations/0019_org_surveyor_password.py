# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0018_fix_org_groups'),
    ]

    operations = [
        migrations.AddField(
            model_name='org',
            name='surveyor_password',
            field=models.CharField(default=None, max_length=128, null=True, help_text='A password that allows users to register as surveyors'),
        ),
    ]
