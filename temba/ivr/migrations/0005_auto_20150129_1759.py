# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('ivr', '0004_auto_20150129_1727'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ivrcall',
            name='contact_urn',
            field=models.ForeignKey(verbose_name='Contact URN', to='contacts.ContactURN', help_text='The URN this call is communicating with'),
            preserve_default=True,
        ),
    ]
