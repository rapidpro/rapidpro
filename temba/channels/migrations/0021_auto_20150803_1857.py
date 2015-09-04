# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations, connection

class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0020_auto_20150714_0258'),
    ]

    def install_updated_increment(apps, schema_editor):
        #language=SQL
        update_trigger = """
            CREATE OR REPLACE FUNCTION temba_decrement_channelcount(_channel_id INTEGER, _count_type VARCHAR(2), _count_day DATE) RETURNS VOID AS $$
              BEGIN
                INSERT INTO channels_channelcount("channel_id", "count_type", "day", "count")
                  VALUES(_channel_id, _count_type, _count_day, -1);
                PERFORM temba_maybe_squash_channelcount(_channel_id, _count_type, _count_day);
              END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION temba_increment_channelcount(_channel_id INTEGER, _count_type VARCHAR(2), _count_day DATE) RETURNS VOID AS $$
              BEGIN
                INSERT INTO channels_channelcount("channel_id", "count_type", "day", "count")
                  VALUES(_channel_id, _count_type, _count_day, 1);
                PERFORM temba_maybe_squash_channelcount(_channel_id, _count_type, _count_day);
              END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION temba_maybe_squash_channelcount(_channel_id INTEGER, _count_type VARCHAR(2), _count_day DATE) RETURNS VOID AS $$
              BEGIN
                IF RANDOM() < .005 THEN
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
        cursor = connection.cursor()
        cursor.execute(update_trigger)

    operations = [
        migrations.AlterUniqueTogether(
           name='channelcount',
           unique_together=set([]),
        ),
        migrations.AlterIndexTogether(
           name='channelcount',
           index_together=set([('channel', 'count_type', 'day')]),
        ),
        migrations.RunPython(
           install_updated_increment,
        ),
    ]
