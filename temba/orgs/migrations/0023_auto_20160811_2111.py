# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0022_update_triggers'),
    ]

    operations = [
        migrations.AddField(
            model_name='org',
            name='multi_org',
            field=models.BooleanField(default=False, help_text='Put this org on the multi org level'),
        ),
        migrations.AlterField(
            model_name='debit',
            name='beneficiary',
            field=models.ForeignKey(related_name='allocations', to='orgs.TopUp', help_text='Optional topup that was allocated with these credits', null=True),
        ),
        migrations.AlterField(
            model_name='debit',
            name='topup',
            field=models.ForeignKey(related_name='debits', to='orgs.TopUp', help_text='The topup these credits are applied against'),
        ),
    ]
