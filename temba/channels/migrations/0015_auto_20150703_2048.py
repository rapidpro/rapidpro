# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations, connection

class Migration(migrations.Migration):

    def install_channellog_trigger(apps, schema_editor):
        """
        Installs a Postgres trigger that will increment or decrement our our success and error
        log counts based on insertion in channels_channellog
        """
        #language=SQL
        install_trigger = """
            CREATE OR REPLACE FUNCTION update_channellog_count() RETURNS TRIGGER AS $$
            BEGIN
              -- ChannelLog being added
              IF TG_OP = 'INSERT' THEN
                -- Error, increment our error count
                IF NEW.is_error THEN
                  UPDATE channels_channel SET error_log_count=error_log_count+1 WHERE id=NEW.channel_id;
                -- Success, increment that count instead
                ELSE
                  UPDATE channels_channel SET success_log_count=success_log_count+1 WHERE id=NEW.channel_id;
                END IF;

              -- ChannelLog being removed
              ELSIF TG_OP = 'DELETE' THEN
                -- Error, decrement our error count
                if OLD.is_error THEN
                  UPDATE channels_channel SET error_log_count=error_log_count-1 WHERE id=OLD.channel_id;
                -- Success, decrement that count instead
                ELSE
                  UPDATE channels_channel SET success_log_count=success_log_count-1 WHERE id=OLD.channel_id;
                END IF;

              -- Updating is_error is forbidden
              ELSIF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'Cannot update is_error or channel_id on ChannelLog events';

              -- Table being cleared, reset all counts
              ELSIF TG_OP = 'TRUNCATE' THEN
                UPDATE channels_channel SET error_log_count=0, success_log_count=0;
              END IF;

              RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;

            -- Install INSERT, UPDATE and DELETE triggers
            DROP TRIGGER IF EXISTS when_channellog_changes_then_update_channel_trg on channels_channellog;
            CREATE TRIGGER when_channellog_changes_then_update_channel_trg
               AFTER INSERT OR DELETE OR UPDATE OF is_error, channel_id
               ON channels_channellog
               FOR EACH ROW
               EXECUTE PROCEDURE update_channellog_count();

            -- Install TRUNCATE trigger
            DROP TRIGGER IF EXISTS when_channellog_truncate_then_update_channel_trg on channels_channellog;
            CREATE TRIGGER when_channellog_truncate_then_update_channel_trg
              AFTER TRUNCATE
              ON channels_channellog
              EXECUTE PROCEDURE update_channellog_count();
        """
        cursor = connection.cursor()
        cursor.execute(install_trigger)

    dependencies = [
        ('channels', '0014_create_channellog_index'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channellog',
            name='channel',
            field=models.ForeignKey(related_name='logs', to='channels.Channel', help_text='The channel the message was sent on'),
            preserve_default=True,
        ),
        migrations.RunPython(
            install_channellog_trigger,
        ),
    ]
