# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0030_auto_20150815_0003'),
    ]

    operations = [
        migrations.AlterField(
            model_name='call',
            name='contact',
            field=models.ForeignKey(related_name='calls', verbose_name='Contact', to='contacts.Contact', help_text='The phone number for this call'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='msg',
            name='contact_urn',
            field=models.ForeignKey(related_name='msgs', verbose_name='Contact URN', to='contacts.ContactURN', help_text='The URN this message is communicating with', null=True),
            preserve_default=True,
        ),
    ]
