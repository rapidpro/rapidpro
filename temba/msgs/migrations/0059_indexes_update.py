# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


INDEX_SQL = """
-- for the Inbox, Flow and Archived folders
CREATE INDEX msgs_msg_visibility_type_created_id_where_inbound
ON msgs_msg(org_id, visibility, msg_type, created_on DESC, id DESC)
WHERE direction = 'I';

DROP INDEX msg_visibility_direction_type_created_inbound;

-- for the Incoming folder (API only)
CREATE INDEX msgs_msg_org_modified_id_where_inbound
ON msgs_msg (org_id, modified_on DESC, id DESC)
WHERE direction = 'I';

DROP INDEX IF EXISTS msg_direction_modified_inbound;

-- for the Outbox folder
CREATE INDEX msgs_msg_org_created_id_where_outbound_visible_outbox
ON msgs_msg(org_id, created_on DESC, id DESC)
WHERE direction = 'O' AND visibility = 'V' AND status IN ('P', 'Q');

DROP INDEX IF EXISTS msgs_msg_outbox_label;

-- for the Sent folder
CREATE INDEX msgs_msg_org_created_id_where_outbound_visible_sent
ON msgs_msg(org_id, created_on DESC, id DESC)
WHERE direction = 'O' AND visibility = 'V' AND status IN ('W', 'S', 'D');

DROP INDEX IF EXISTS msgs_msg_sent_label;

-- for the Failed folder
CREATE INDEX msgs_msg_org_created_id_where_outbound_visible_failed
ON msgs_msg(org_id, created_on DESC, id DESC)
WHERE direction = 'O' AND visibility = 'V' AND status = 'F';

DROP INDEX IF EXISTS msgs_msg_failed_label;

-- for the Scheduled folder and API view of broadcasts
CREATE INDEX msgs_broadcasts_org_created_id_where_active
ON msgs_broadcast(org_id, created_on DESC, id DESC)
WHERE is_active = true;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0058_update_triggers'),
    ]

    operations = [
        migrations.RunSQL(INDEX_SQL)
    ]
