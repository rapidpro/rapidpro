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
            CREATE OR REPLACE FUNCTION temba_decrement_dailychannelcount(_channel_id INTEGER, _count_type VARCHAR(2), _count_day DATE) RETURNS VOID AS $$
              BEGIN
                UPDATE channels_dailychannelcount SET "count"="count"-1
                  WHERE "channel_id"=_channel_id AND "count_type"=_count_type AND "day"=_count_day;
              END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION temba_increment_dailychannelcount(_channel_id INTEGER, _count_type VARCHAR(2), _count_day DATE) RETURNS VOID AS $$
              BEGIN
                LOOP
                  -- first try incrementing
                  UPDATE channels_dailychannelcount SET "count"="count"+1
                    WHERE "channel_id"=_channel_id AND "count_type"=_count_type AND "day"=_count_day;
                  IF found THEN
                    RETURN;
                  END IF;

                  -- not there, so try to insert the key
                  -- if someone else inserts the same key concurrently,
                  -- we will get a unique-key failure
                  BEGIN
                    INSERT INTO channels_dailychannelcount("channel_id", "count_type", "day", "count")
                      VALUES(_channel_id, _count_type, _count_day, 1);
                    RETURN;
                  EXCEPTION WHEN unique_violation THEN
                    -- Do nothing, and loop to try the UPDATE again.
                  END;
                END LOOP;
              END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION temba_update_dailychannelcount() RETURNS TRIGGER AS $$
            DECLARE
              is_test boolean;
            BEGIN
              -- Message being updated
              IF TG_OP = 'INSERT' THEN
                -- Return if there is no channel on this message
                IF NEW.channel_id IS NULL THEN
                  RETURN NULL;
                END IF;

                -- Find out if this is a test contact
                SELECT contacts_contact.is_test INTO STRICT is_test FROM contacts_contact WHERE id=NEW.contact_id;

                -- Return if it is
                IF is_test THEN
                  RETURN NULL;
                END IF;

                -- If this is an incoming message, without message type, then increment that count
                IF NEW.direction = 'I' THEN
                  -- This is a voice message, increment that count
                  IF NEW.msg_type = 'V' THEN
                    PERFORM temba_increment_dailychannelcount(NEW.channel_id, 'IV', NEW.created_on::date);
                  -- Otherwise, this is a normal message
                  ELSE
                    PERFORM temba_increment_dailychannelcount(NEW.channel_id, 'IM', NEW.created_on::date);
                  END IF;

                -- This is an outgoing message
                ELSIF NEW.direction = 'O' THEN
                  -- This is a voice message, increment that count
                  IF NEW.msg_type = 'V' THEN
                    PERFORM temba_increment_dailychannelcount(NEW.channel_id, 'OV', NEW.created_on::date);
                  -- Otherwise, this is a normal message
                  ELSE
                    PERFORM temba_increment_dailychannelcount(NEW.channel_id, 'OM', NEW.created_on::date);
                  END IF;

                END IF;

              -- Assert that updates aren't happening that we don't approve of
              ELSIF TG_OP = 'UPDATE' THEN
                -- If the direction is changing, blow up
                IF NEW.direction <> OLD.direction THEN
                  RAISE EXCEPTION 'Cannot change direction on messages';
                END IF;

                -- Cannot move from IVR to Text, or IVR to Text
                IF (OLD.msg_type <> 'V' AND NEW.msg_type = 'V') OR (OLD.msg_type = 'V' AND NEW.msg_type <> 'V') THEN
                  RAISE EXCEPTION 'Cannot change a message from voice to something else or vice versa';
                END IF;

                -- Cannot change created_on
                IF NEW.created_on <> OLD.created_on THEN
                  RAISE EXCEPTION 'Cannot change created_on on messages';
                END IF;

              -- Message is being deleted, we need to decrement our count
              ELSIF TG_OP = 'DELETE' THEN
                -- Find out if this is a test contact
                SELECT contacts_contact.is_test INTO STRICT is_test FROM contacts_contact WHERE id=OLD.contact_id;

                -- Escape out if this is a test contact
                IF is_test THEN
                  RETURN NULL;
                END IF;

                -- This is an incoming message
                IF OLD.direction = 'I' THEN
                  -- And it is voice
                  IF OLD.msg_type = 'V' THEN
                    PERFORM temba_decrement_dailychannelcount(OLD.channel_id, 'IV', OLD.created_on::date);
                  -- Otherwise, this is a normal message
                  ELSE
                    PERFORM temba_decrement_dailychannelcount(OLD.channel_id, 'IM', OLD.created_on::date);
                  END IF;

                -- This is an outgoing message
                ELSIF OLD.direction = 'O' THEN
                  -- And it is voice
                  IF OLD.msg_type = 'V' THEN
                    PERFORM temba_decrement_dailychannelcount(OLD.channel_id, 'OV', OLD.created_on::date);
                  -- Otherwise, this is a normal message
                  ELSE
                    PERFORM temba_decrement_dailychannelcount(OLD.channel_id, 'OM', OLD.created_on::date);
                  END IF;
                END IF;

              -- Table being cleared, reset all counts
              ELSIF TG_OP = 'TRUNCATE' THEN
                TRUNCATE channels_dailychannelcount;
              END IF;

              RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;

            -- Install INSERT, UPDATE and DELETE triggers
            DROP TRIGGER IF EXISTS temba_msg_update_dailychannelcount on msgs_msg;
            CREATE TRIGGER temba_msg_update_dailychannelcount
               AFTER INSERT OR DELETE OR UPDATE OF direction, msg_type, created_on
               ON msgs_msg
               FOR EACH ROW
               EXECUTE PROCEDURE temba_update_dailychannelcount();

            -- Install TRUNCATE trigger
            DROP TRIGGER IF EXISTS temba_msg_clear_dailychannelcount on msgs_msg;
            CREATE TRIGGER temba_msg_clear_dailychannelcount
              AFTER TRUNCATE
              ON msgs_msg
              EXECUTE PROCEDURE temba_update_dailychannelcount();
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