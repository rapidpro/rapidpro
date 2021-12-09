# Generated by Django 2.2.24 on 2021-11-03 13:10
import json
import logging
from urllib.parse import parse_qs

from django.db import migrations, models
from django.db.models import Q


logger = logging.getLogger(__name__)
add_trigger = """
CREATE OR REPLACE FUNCTION temba_update_channel_segments_count() RETURNS TRIGGER AS $$
DECLARE
    msg_direction varchar(1);
    segments_count integer;
BEGIN
  -- Message being updated
  IF TG_OP = 'INSERT' THEN
    -- Return if there is no channel on this message
    IF NEW.channel_id IS NULL OR NEW.msg_id IS NULL THEN
      RETURN NULL;
    END IF;

    SELECT direction, segments INTO msg_direction, segments_count
    FROM msgs_msg WHERE msgs_msg.id = NEW.msg_id;
    IF segments_count = 0 OR segments_count IS NULL THEN
      RETURN NULL;
    END IF;

    IF msg_direction = 'I' THEN
      PERFORM temba_insert_channelcount(NEW.channel_id, 'IMS', NEW.created_on::date, segments_count);
    ELSIF msg_direction = 'O' THEN
      PERFORM temba_insert_channelcount(NEW.channel_id, 'OMS', NEW.created_on::date, segments_count);
    END IF;

  -- Clean up counts when we are doing a real delete
  ELSIF TG_OP = 'DELETE' THEN
    IF OLD.channel_id IS NULL OR OLD.msg_id IS NULL THEN
      RETURN NULL;
    END IF;

    SELECT direction, segments INTO msg_direction, segments_count
    FROM msgs_msg WHERE msgs_msg.id = OLD.msg_id;
    IF segments_count = 0 OR segments_count IS NULL THEN
      RETURN NULL;
    END IF;

    IF msg_direction = 'I' THEN
      PERFORM temba_insert_channelcount(OLD.channel_id, 'IMS', OLD.created_on::date, segments_count * -1);
    ELSIF msg_direction = 'O' THEN
      PERFORM temba_insert_channelcount(OLD.channel_id, 'OMS', OLD.created_on::date, segments_count * -1);
    END IF;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER temba_channellog_update_segments_channelcount
   AFTER INSERT OR DELETE
   ON channels_channellog
   FOR EACH ROW
   EXECUTE PROCEDURE temba_update_channel_segments_count();
"""

remove_trigger = """
DROP TRIGGER IF EXISTS temba_channellog_update_segments_channelcount ON channels_channellog;
DROP FUNCTION IF EXISTS temba_update_channel_segments_count;
"""


def get_existing_messages_segments(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Msg = apps.get_model("msgs", "Msg")
    ChannelLog = apps.get_model("channels", "ChannelLog")
    ChannelCount = apps.get_model("channels", "ChannelCount")
    msgs_to_update = dict()
    channel_counts = list()
    logs = (
        ChannelLog.objects.using(db_alias)
        .filter(
            channel__channel_type__in=("T", "TW", "TMA", "TMS", "SW"),
            is_error=False,
            msg__isnull=False,
        )
        .exclude(Q(response__isnull=True) | Q(response=""))
    )
    logs_count = len(logs)
    for index, log in enumerate(logs):
        logger.warning(f"Getting segments count from Twilio logs - {round(index / logs_count, 2)}%")
        if log.msg.direction == "O":
            try:
                response_str = log.response.split("\r\n\r\n")[-1]
                response_json = json.loads(response_str)
                num_segments = int(response_json["num_segments"])
                msg = msgs_to_update[log.msg.id] if log.msg.id in msgs_to_update.keys() else log.msg
                msg.segments = num_segments + (msg.segments if msg.segments else 0)
                msgs_to_update[msg.id] = msg
                channel_counts.append(
                    ChannelCount(
                        day=msg.created_on,
                        channel=msg.channel,
                        count=num_segments,
                        count_type="OMS",
                    )
                )
            except (IndexError, KeyError, json.JSONDecodeError) as e:
                logger.error(str(e))
        else:
            try:
                response_str = log.request.split("\r\n\r\n")[-1]
                response_qs = parse_qs(response_str)
                num_segments = response_qs.get("NumSegments")
                if not num_segments:
                    continue
                num_segments = int(num_segments[0])
                msg = msgs_to_update[log.msg.id] if log.msg.id in msgs_to_update.keys() else log.msg
                msg.segments = num_segments + (msg.segments if msg.segments else 0)
                msgs_to_update[msg.id] = msg
                channel_counts.append(
                    ChannelCount(
                        day=msg.created_on,
                        channel=msg.channel,
                        count=num_segments,
                        count_type="IMS",
                    )
                )
            except (IndexError, KeyError, ValueError) as e:
                logger.error(str(e))

    Msg.objects.using(db_alias).bulk_update(msgs_to_update.values(), ["segments"])
    ChannelCount.objects.using(db_alias).bulk_create(channel_counts)


def remove_segments_count(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Msg = apps.get_model("msgs", "Msg")
    ChannelCount = apps.get_model("channels", "ChannelCount")
    ChannelCount.objects.using(db_alias).filter(count_type__in=["IMS", "OMS"]).delete()
    Msg.objects.using(db_alias).filter(segments__isnull=False, segments__gt=0).update(segments=None)


class Migration(migrations.Migration):
    dependencies = [
        ("channels", "0128_auto_20211022_1122"),
        ("msgs", "0147_msg_segments"),
    ]

    operations = [
        migrations.AlterField(
            model_name="channelcount",
            name="count_type",
            field=models.CharField(
                choices=[
                    ("IM", "Incoming Message"),
                    ("OM", "Outgoing Message"),
                    ("IMS", "Incoming Message Segments"),
                    ("OMS", "Outgoing Message Segments"),
                    ("IV", "Incoming Voice"),
                    ("OV", "Outgoing Voice"),
                    ("LS", "Success Log Record"),
                    ("LE", "Error Log Record"),
                ],
                help_text="What type of message this row is counting",
                max_length=3,
            ),
        ),
        migrations.RunSQL(add_trigger, remove_trigger),
        migrations.RunPython(get_existing_messages_segments, remove_segments_count),
    ]
