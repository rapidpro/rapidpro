# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations, connection

#language=SQL
TRIGGER_SQL = """
    CREATE OR REPLACE FUNCTION temba_maybe_squash_channelcount(_channel_id INTEGER, _count_type VARCHAR(2), _count_day DATE) RETURNS VOID AS $$
      BEGIN
        IF RANDOM() < .001 THEN
          IF _count_day IS NULL THEN
            WITH removed as (DELETE FROM channels_channelcount
              WHERE "channel_id" = _channel_id AND "count_type" = _count_type AND "day" IS NULL
              RETURNING "count")
              INSERT INTO channels_channelcount("channel_id", "count_type", "count")
              VALUES (_channel_id, _count_type, GREATEST(0, (SELECT SUM("count") FROM removed)));
          ELSE
            WITH removed as (DELETE FROM channels_channelcount
              WHERE "channel_id" = _channel_id AND "count_type" = _count_type AND "day" = _count_day
              RETURNING "count")
              INSERT INTO channels_channelcount("channel_id", "count_type", "day", "count")
              VALUES (_channel_id, _count_type, _count_day, GREATEST(0, (SELECT SUM("count") FROM removed)));
          END IF;
        END IF;
      END;
    $$ LANGUAGE plpgsql;
"""

class Migration(migrations.Migration):
    dependencies = [
        ('channels', '0021_auto_20150803_1857'),
    ]

    operations = [
        migrations.RunSQL(TRIGGER_SQL),
    ]
