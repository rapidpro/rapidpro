# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations, connection
from django.db.models import Count


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0016_dailychannelcount'),
    ]

    def calculate_counts(apps, schema_editor):
        """
        Iterate across all our channels, calculate our message counts for each category
        """
        DailyChannelCount = apps.get_model('channels', 'DailyChannelCount')
        Channel = apps.get_model('channels', 'Channel')
        Msg = apps.get_model('msgs', 'Msg')

        def add_daily_counts(channel, count_type, daily_counts):
            for daily_count in daily_counts:
                print "Adding %d - %s - %s" % (channel.id, count_type, str(daily_count))

                DailyChannelCount.objects.create(channel=channel, count_type=count_type,
                                                 day=daily_count['created'], count=daily_count['count'])

        for channel in Channel.objects.all():
            # incoming msgs
            daily_counts = Msg.objects.filter(channel=channel, contact__is_test=False, direction='I')\
                                      .exclude(msg_type='V')\
                                      .extra({'created': "date(msgs_msg.created_on)"})\
                                      .values('created')\
                                      .annotate(count=Count('id'))\
                                      .order_by('created')
            add_daily_counts(channel, 'IM', daily_counts)

            # outgoing msgs
            daily_counts = Msg.objects.filter(channel=channel, contact__is_test=False, direction='O')\
                                      .exclude(msg_type='V')\
                                      .extra({'created': "date(msgs_msg.created_on)"})\
                                      .values('created')\
                                      .annotate(count=Count('id'))\
                                      .order_by('created')
            add_daily_counts(channel, 'OM', daily_counts)

            # incoming voice
            daily_counts = Msg.objects.filter(channel=channel, contact__is_test=False, direction='I')\
                                      .filter(msg_type='V')\
                                      .extra({'created': "date(msgs_msg.created_on)"})\
                                      .values('created')\
                                      .annotate(count=Count('id'))\
                                      .order_by('created')
            add_daily_counts(channel, 'IV', daily_counts)

            # outgoing voice
            daily_counts = Msg.objects.filter(channel=channel, contact__is_test=False, direction='O')\
                                      .filter(msg_type='V')\
                                      .extra({'created': "date(msgs_msg.created_on)"})\
                                      .values('created')\
                                      .annotate(count=Count('id'))\
                                      .order_by('created')
            add_daily_counts(channel, 'OV', daily_counts)

    def install_channelcount_trigger(apps, schema_editor):
        """
        Installs a Postgres trigger that will increment our daily counts as messages inserted.
        """
        #language=SQL
        install_trigger = """
            CREATE OR REPLACE FUNCTION decrement_daily_channel_count(channel_id, count_type, count_day) RETURNS TRIGGER AS $$
              BEGIN
                UPDATE channels_dailychannelcount SET "count"="count"-1
                  WHERE channel_id=channel_id AND count_type=count_type AND "day" = count_day;
              END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION increment_daily_channel_count(channel_id, count_type, count_day) RETURNS TRIGGER AS $$
              BEGIN
                LOOP
                  -- first try incrementing
                  updated = UPDATE channels_dailychannelcount SET "count"="count"+1
                              WHERE channel_id=channel_id AND count_type=count_type AND "day" = count_day;
                  IF found THEN
                    RETURN;
                  END IF;

                  -- not there, so try to insert the key
                  -- if someone else inserts the same key concurrently,
                  -- we will get a unique-key failure
                  BEGIN
                    INSERT INTO channels_dailychannelcount(channel_id, count_type, "day", "count")
                      VALUES(channel_id, count_type, count_day, 1);
                    RETURN;
                  EXCEPTION WHEN unique_violation THEN
                    -- Do nothing, and loop to try the UPDATE again.
                  END;
                END LOOP;
              END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION update_daily_channel_count() RETURNS TRIGGER AS $$
            DECLARE
              is_test boolean;
            BEGIN
              -- Return if there is no channel on this message
              IF NEW.channel_id IS NULL THEN
                RETURN NULL;
              END IF;

              -- Message being updated
              IF TG_OP = 'INSERT' THEN
                -- Find out if this is a test contact
                SELECT contacts_contact.is_test INTO STRICT is_test FROM contacts_contact WHERE id=NEW.contact_id;

                -- Return if it is
                IF is_test THEN
                  RETURN NULL;
                END IF;

                -- If this is an incoming message, without message type, then increment that count
                IF NEW.direction = 'I' THEN
                  -- This is a voice message, increment that count
                  IF NEW.msg_type IS 'V' THEN
                    SELECT increment_daily_channel_count(NEW.channel_id, "IV", DAY(NEW.created_on))
                  -- Otherwise, this is a normal message
                  ELSE
                    SELECT increment_daily_channel_count(NEW.channel_id, "IM", DAY(NEW.created_on))
                  END IF;

                -- This is an outgoing message
                ELSIF NEW.direction = 'O' THEN
                  -- This is a voice message, increment that count
                  IF NEW.msg_type IS 'V' THEN
                    SELECT increment_daily_channel_count(NEW.channel_id, "OV", DAY(NEW.created_on))
                  -- Otherwise, this is a normal message
                  ELSE
                    SELECT increment_daily_channel_count(NEW.channel_id, "OM", DAY(NEW.created_on))
                  END IF;

                END IF;
              END IF;

              RETURN NULL;

              -- Assert that updates aren't happening that we don't approve of
              ELSIF TG_OP = 'UPDATE' THEN
                -- If the direction is changing, blow up
                IF NEW.direction != OLD.direction
                  RAISE ERROR 'Cannot change direction on messages'

                -- Cannot move from IVR to Text
                IF NEW.msg_type = 'V' and OLD.msg_type != 'V' THEN
                  RAISE ERROR 'Cannot change a message from voice to something else'

                -- Cannot change created_on
                IF NEW.created_on != OLD.created_on
                  RAISE ERROR 'Cannot change created_on on messages'

              -- Message is being deleted, we need to decrement our count
              ELSIF TG_OP = 'DELETE' THEN
                -- Find out if this is a test contact
                SELECT contacts_contact.is_test INTO STRICT is_test FROM contacts_contact WHERE id=OLD.contact_id;

                -- Escape out if we are
                IF is_test THEN
                  RETURN NULL;
                END IF;

                -- This is an incoming message
                IF NEW.direction = 'I' THEN
                  -- And it is voice
                  IF NEW.msg_type IS 'V' THEN
                    SELECT decrement_daily_channel_count(NEW.channel_id, "IV", DAY(NEW.created_on))
                  -- Otherwise, this is a normal message
                  ELSE
                    SELECT decrement_daily_channel_count(NEW.channel_id, "IM", DAY(NEW.created_on))
                  END IF;

                -- This is an outgoing message
                ELSIF NEW.direction = 'O' THEN
                  -- And it is voice
                  IF NEW.msg_type IS 'V' THEN
                    SELECT decrement_daily_channel_count(NEW.channel_id, "OV", DAY(NEW.created_on))
                  -- Otherwise, this is a normal message
                  ELSE
                    SELECT decrement_daily_channel_count(NEW.channel_id, "OM", DAY(NEW.created_on))
                  END IF;

              -- Table being cleared, reset all counts
              ELSIF TG_OP = 'TRUNCATE' THEN
                UPDATE channels_dailychannelcount SET count=0;
              END IF;

              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            -- Install INSERT, UPDATE and DELETE triggers
            DROP TRIGGER IF EXISTS when_msgs_change_update_channel_counts on msgs_msg;
            CREATE TRIGGER when_msgs_change_update_channel_counts
               AFTER INSERT OR DELETE OR UPDATE OF direction, msg_type, created_on
               ON msgs_msg
               FOR EACH ROW
               EXECUTE PROCEDURE update_daily_channel_count();

            -- Install TRUNCATE trigger
            DROP TRIGGER IF EXISTS when_msgs_truncate_update_channel_counts on msgs_msg;
            CREATE TRIGGER when_msgs_truncate_update_channel_counts
              AFTER TRUNCATE
              ON msgs_msg
              EXECUTE PROCEDURE update_daily_channel_count();
        """
        cursor = connection.cursor()
        cursor.execute(install_trigger)

    operations = [
        migrations.RunPython(
            calculate_counts,
        ),
        migrations.RunPython(
            install_channelcount_trigger,
        ),
    ]