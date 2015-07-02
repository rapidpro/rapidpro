# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0018_msg_type_nullable'),
    ]

    operations = [
        # Remove labels from test messages - something we no longer support
        migrations.RunSQL("""
            DELETE FROM msgs_msg_labels
            USING msgs_msg, contacts_contact
            WHERE msgs_msg_labels.msg_id = msgs_msg.id
              AND msgs_msg.contact_id = contacts_contact.id
              AND contacts_contact.is_test = TRUE;
        """)
    ]
