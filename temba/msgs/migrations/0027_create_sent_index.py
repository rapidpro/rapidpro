# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations

# language=SQL
CREATE_SQL = """
CREATE INDEX msgs_msg_sent_label ON msgs_msg(org_id, created_on DESC)
WHERE direction = 'O' AND visibility = 'V' AND status IN ('W', 'S', 'D');
"""

# language=SQL
DROP_SQL = """
DROP INDEX msgs_msg_sent_label;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0026_system_label_triggers'),
    ]

    operations = [
        migrations.RunSQL(CREATE_SQL, DROP_SQL)
    ]
