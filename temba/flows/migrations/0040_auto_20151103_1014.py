# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0039_auto_20151028_1648'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flowstart',
            name='contacts',
            field=models.ManyToManyField(help_text='Contacts that will start the flow', to='contacts.Contact'),
        ),
        migrations.AlterField(
            model_name='flowstart',
            name='groups',
            field=models.ManyToManyField(help_text='Groups that will start the flow', to='contacts.ContactGroup'),
        ),
        migrations.AlterField(
            model_name='flowstep',
            name='messages',
            field=models.ManyToManyField(help_text='Any messages that are associated with this step (either sent or received)', related_name='steps', to='msgs.Msg'),
        ),
    ]
