# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, connection
from django.db.transaction import set_autocommit, commit

# language=SQL
CREATE_SQL = """
CREATE INDEX channels_channellog_channel_created_on ON channels_channellog(channel_id, created_on desc);
"""

# language=SQL
DROP_SQL = """
DROP INDEX channels_channellog_channel_created_on;
"""

class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0013_auto_20150703_1837'),
    ]

    operations = [
        migrations.RunSQL(CREATE_SQL, DROP_SQL)
    ]
