# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0067_auto_20161005_0731'),
    ]

    operations = [
        migrations.AlterField(
            model_name='broadcast',
            name='status',
            field=models.CharField(default='I', help_text='The current status for this broadcast', max_length=1, verbose_name='Status', choices=[('I', 'Initializing'), ('P', 'Pending'), ('Q', 'Queued'), ('W', 'Wired'), ('S', 'Sent'), ('D', 'Delivered'), ('H', 'Handled'), ('E', 'Error Sending'), ('F', 'Failed Sending'), ('R', 'Resent message'), ('X', 'Interrupt message'), ('T', 'Custom trigger')]),
        ),
        migrations.AlterField(
            model_name='msg',
            name='status',
            field=models.CharField(default='P', choices=[('I', 'Initializing'), ('P', 'Pending'), ('Q', 'Queued'), ('W', 'Wired'), ('S', 'Sent'), ('D', 'Delivered'), ('H', 'Handled'), ('E', 'Error Sending'), ('F', 'Failed Sending'), ('R', 'Resent message'), ('X', 'Interrupt message'), ('T', 'Custom trigger')], max_length=1, help_text='The current status for this message', verbose_name='Status', db_index=True),
        ),
    ]
