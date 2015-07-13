# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0022_exportflowresultstask_is_finished'),
    ]

    operations = [
        migrations.AddField(
            model_name='ruleset',
            name='ruleset_type',
            field=models.CharField(default='wait_message', help_text='The type of ruleset', max_length=16, choices=[('wait_message', 'Wait for message'), ('wait_recording', 'Wait for recording'), ('wait_digit', 'Wait for digit'), ('wait_digits', 'Wait for digits'), ('webhook', 'Webhook'), ('flow_field', 'Split on flow field'), ('contact_field', 'Split on contact field'), ('expression', 'Split by expression')]),
            preserve_default=True,
        ),
    ]
