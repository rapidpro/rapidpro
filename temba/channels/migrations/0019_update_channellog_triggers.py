# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations, connection

class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0018_auto_20150710_2047'),
    ]

    def clear_channel_logs(apps, schema_editor):
        """
        Clear out all our channel logs, we'll start anew
        """
        ChannelCount = apps.get_model('channels', 'ChannelCount')
        ChannelCount.objects.all().delete()

    def install_channellog_trigger(apps, schema_editor):
        """
        Installs a Postgres trigger that will increment or decrement our our success and error
        log counts based on insertion in channels_channellog
        """
        #language=SQL
        install_trigger = """
            CREATE OR REPLACE FUNCTION temba_update_channellog_count() RETURNS TRIGGER AS $$
            BEGIN
              -- ChannelLog being added
              IF TG_OP = 'INSERT' THEN
                -- Error, increment our error count
                IF NEW.is_error THEN
                  PERFORM temba_increment_channelcount(NEW.channel_id, 'LE', NULL::date);
                -- Success, increment that count instead
                ELSE
                  PERFORM temba_increment_channelcount(NEW.channel_id, 'LS', NULL::date);
                END IF;

              -- ChannelLog being removed
              ELSIF TG_OP = 'DELETE' THEN
                -- Error, decrement our error count
                if OLD.is_error THEN
                  PERFORM temba_decrement_channelcount(OLD.channel_id, 'LE', NULL::date);
                -- Success, decrement that count instead
                ELSE
                  PERFORM temba_decrement_channelcount(OLD.channel_id, 'LS', NULL::date);
                END IF;

              -- Updating is_error is forbidden
              ELSIF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'Cannot update is_error or channel_id on ChannelLog events';

              -- Table being cleared, reset all counts
              ELSIF TG_OP = 'TRUNCATE' THEN
                UPDATE channels_channel SET count=0 WHERE count_type IN ('LE', 'LS');
              END IF;

              RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;

            -- Install INSERT, UPDATE and DELETE triggers
            DROP TRIGGER IF EXISTS temba_channellog_update_channelcount on channels_channellog;
            CREATE TRIGGER temba_channellog_update_channelcount
               AFTER INSERT OR DELETE OR UPDATE OF is_error, channel_id
               ON channels_channellog
               FOR EACH ROW
               EXECUTE PROCEDURE temba_update_channellog_count();

            -- Install TRUNCATE trigger
            DROP TRIGGER IF EXISTS temba_channellog_truncate_channelcount on channels_channellog;
            CREATE TRIGGER temba_channellog_truncate_channelcount
              AFTER TRUNCATE
              ON channels_channellog
              EXECUTE PROCEDURE temba_update_channellog_count();
        """
        cursor = connection.cursor()
        cursor.execute(install_trigger)

    operations = [
         migrations.RunPython(
            clear_channel_logs,
        ),
        migrations.RunPython(
            install_channellog_trigger,
        ),
    ]
