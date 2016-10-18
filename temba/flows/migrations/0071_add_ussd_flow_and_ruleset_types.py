# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0070_remove_exportflowresultstask_host'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flow',
            name='flow_type',
            field=models.CharField(default='F', help_text='The type of this flow', max_length=1, choices=[('F', 'Message flow'), ('M', 'Single Message Flow'), ('V', 'Phone call flow'), ('S', 'Android Survey'), ('U', 'USSD flow')]),
        ),
        migrations.AlterField(
            model_name='ruleset',
            name='ruleset_type',
            field=models.CharField(help_text='The type of ruleset', max_length=16, null=True, choices=[('wait_message', 'Wait for message'), ('wait_menu', 'Wait for USSD menu'), ('wait_ussd', 'Wait for USSD message'), ('wait_recording', 'Wait for recording'), ('wait_digit', 'Wait for digit'), ('wait_digits', 'Wait for digits'), ('subflow', 'Subflow'), ('webhook', 'Webhook'), ('resthook', 'Resthook'), ('airtime', 'Transfer Airtime'), ('form_field', 'Split by message form'), ('contact_field', 'Split on contact field'), ('expression', 'Split by expression')]),
        ),
    ]
