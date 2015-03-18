# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0005_auto_20141210_0208'),
        ('ivr', '0002_auto_20141126_2054'),
    ]

    operations = [
        migrations.AddField(
            model_name='ivrcall',
            name='contact_urn',
            field=models.ForeignKey(verbose_name='Contact URN', to='contacts.ContactURN', help_text='The URN this call is communicating with', null=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='ivrcall',
            name='duration',
            field=models.IntegerField(default=0, help_text='The length of this call in seconds', null=True),
            preserve_default=True,
        ),
    ]
