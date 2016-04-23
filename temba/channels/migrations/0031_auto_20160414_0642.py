# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0030_update_triggers'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channel',
            name='channel_type',
            field=models.CharField(default='A', help_text='Type of this channel, whether Android, Twilio or SMSC', max_length=3, verbose_name='Channel Type', choices=[('AT', "Africa's Talking"), ('A', 'Android'), ('BM', 'Blackmyna'), ('CT', 'Clickatell'), ('EX', 'External'), ('FB', 'Facebook'), ('HX', 'High Connection'), ('H9', 'Hub9'), ('IB', 'Infobip'), ('JS', 'Jasmin'), ('KN', 'Kannel'), ('M3', 'M3 Tech'), ('MB', 'Mblox'), ('NX', 'Nexmo'), ('PL', 'Plivo'), ('SQ', 'Shaqodoon'), ('SC', 'SMSCentral'), ('ST', 'Start Mobile'), ('TG', 'Telegram'), ('T', 'Twilio'), ('TMS', 'Twilio Messaging Service'), ('TT', 'Twitter'), ('VB', 'Verboice'), ('VM', 'Vumi'), ('YO', 'Yo!'), ('ZV', 'Zenvia')]),
        ),
    ]
