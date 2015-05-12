# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0008_auto_20150422_1828'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channel',
            name='channel_type',
            field=models.CharField(default='A', help_text='Type of this channel, whether Android, Twilio or SMSC', max_length=3, verbose_name='Channel Type', choices=[('A', 'Android'), ('T', 'Twilio'), ('AT', "Africa's Talking"), ('ZV', 'Zenvia'), ('NX', 'Nexmo'), ('IB', 'Infobip'), ('VB', 'Verboice'), ('H9', 'Hub9'), ('VM', 'Vumi'), ('KN', 'Kannel'), ('EX', 'External'), ('TT', 'Twitter'), ('CT', 'Clickatell'), ('PL', 'Plivo'), ('SQ', 'Shaqodoon'), ('HX', 'High Connection'), ('BM', 'Blackmyna')]),
            preserve_default=True,
        ),
    ]
