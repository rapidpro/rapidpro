# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


#language=SQL
TRIGGER_SQL = """
    CREATE OR REPLACE FUNCTION temba_maybe_squash_channelcount(_channel_id INTEGER, _count_type VARCHAR(2), _count_day DATE) RETURNS VOID AS $$
      BEGIN
        IF RANDOM() < .001 THEN
          -- Obtain a lock on the channel so that two threads don't enter this update at once
          PERFORM "id" FROM channels_channel WHERE "id" = _channel_id FOR UPDATE;

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
        ('channels', '0023_auto_20150916_2138'),
    ]

    operations = [
        migrations.RunSQL(TRIGGER_SQL)
    ]
