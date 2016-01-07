# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0017_install_label_triggers'),
    ]

    operations = [
        migrations.AlterField(
            model_name='msg',
            name='msg_type',
            field=models.CharField(help_text='The type of this message', max_length=1, null=True, verbose_name='Message Type', choices=[('I', 'Inbox Message'), ('F', 'Flow Message'), ('V', 'IVR Message')]),
            preserve_default=True,
        ),
    ]
