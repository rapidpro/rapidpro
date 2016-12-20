# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.utils.timezone
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0025_auto_20161026_1502'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='debit',
            name='is_active',
        ),
        migrations.RemoveField(
            model_name='debit',
            name='modified_by',
        ),
        migrations.RemoveField(
            model_name='debit',
            name='modified_on',
        ),
        migrations.AlterField(
            model_name='debit',
            name='created_by',
            field=models.ForeignKey(related_name='debits_created', to=settings.AUTH_USER_MODEL, help_text='The user which originally created this item', null=True),
        ),
        migrations.AlterField(
            model_name='debit',
            name='created_on',
            field=models.DateTimeField(default=django.utils.timezone.now, help_text='When this item was originally created'),
        ),
    ]
