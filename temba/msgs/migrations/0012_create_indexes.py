# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0011_remove_exportmessagestask_filename'),
    ]

    operations = [
        # allows for fast lookup of all inbound messages (inbox, flows, archived)
        migrations.RunSQL("CREATE INDEX msg_visibility_direction_type_created_inbound ON "
                          "msgs_msg(org_id, visibility, direction, msg_type, created_on DESC) "
                          "WHERE direction = 'I';",
                          "DROP INDEX msg_visibility_direction_type_created_inbound;"),

        # allows for fast lookup of failed outbound messages
        migrations.RunSQL("CREATE INDEX msgs_msg_org_failed_created_on ON "
                          "msgs_msg(org_id, direction, visibility, created_on DESC) "
                          "WHERE status = 'F';",
                          "DROP INDEX msgs_msg_org_failed_created_on;")
    ]
