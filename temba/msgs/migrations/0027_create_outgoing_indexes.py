# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations

# language=SQL
CREATE_SQL = """
DROP INDEX IF EXISTS msgs_msg_org_failed_created_on;
DROP INDEX IF EXISTS msgs_msg_sent_label;
DROP INDEX IF EXISTS msgs_msg_outbox_label;
DROP INDEX IF EXISTS msgs_msg_failed_label;

CREATE INDEX msgs_msg_outbox_label ON msgs_msg(org_id, created_on DESC)
WHERE direction = 'O' AND visibility = 'V' AND status IN ('P', 'Q');

CREATE INDEX msgs_msg_sent_label ON msgs_msg(org_id, created_on DESC)
WHERE direction = 'O' AND visibility = 'V' AND status IN ('W', 'S', 'D');

CREATE INDEX msgs_msg_failed_label ON msgs_msg(org_id, created_on DESC)
WHERE direction = 'O' AND visibility = 'V' AND status = 'F';
"""

# language=SQL
DROP_SQL = """
DROP INDEX msgs_msg_sent_label;
DROP INDEX msgs_msg_outbox_label;
DROP INDEX msgs_msg_failed_label;

CREATE INDEX msgs_msg_org_failed_created_on ON msgs_msg(org_id, direction, visibility, created_on DESC)
WHERE status = 'F';
"""


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0026_system_label_triggers'),
    ]

    operations = [
        migrations.RunSQL(CREATE_SQL, DROP_SQL)
    ]
