# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0005_auto_20150416_0729'),
    ]

    operations = [
        migrations.CreateModel(
            name='TopUpCredits',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('used', models.IntegerField(help_text='How many credits were used, can be negative')),
                ('topup', models.ForeignKey(help_text='The topup these credits are being used against', to='orgs.TopUp')),
            ],
            options={
            },
            bases=(models.Model,),
        ),
    ]
