# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0071_add_ussd_flow_and_ruleset_types'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ruleset',
            name='ruleset_type',
            field=models.CharField(help_text='The type of ruleset', max_length=16, null=True, choices=[('wait_message', 'Wait for message'), ('wait_menu', 'Wait for USSD menu'), ('wait_ussd', 'Wait for USSD message'), ('wait_recording', 'Wait for recording'), ('wait_digit', 'Wait for digit'), ('wait_digits', 'Wait for digits'), ('subflow', 'Subflow'), ('webhook', 'Webhook'), ('resthook', 'Resthook'), ('airtime', 'Transfer Airtime'), ('form_field', 'Split by message form'), ('contact_field', 'Split on contact field'), ('expression', 'Split by expression'), ('subflow', 'Split Randomly')]),
        ),
    ]
