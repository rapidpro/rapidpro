import os
import logging
import pytz

from datetime import datetime

from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.migrator import Migrator
from temba.orgs.models import TopUp, TopUpCredits, Language
from temba.contacts.models import ContactField, Contact, ContactGroup, ContactURN
from temba.channels.models import Channel, ChannelCount, SyncEvent, ChannelEvent, ChannelLog
from temba.schedules.models import Schedule
from temba.msgs.models import Msg, Label, Broadcast
from temba.orgs.models import Org
from temba.flows.models import (
    Flow,
    FlowLabel,
    FlowRun,
    FlowStart,
    FlowCategoryCount,
    FlowNodeCount,
    FlowPathCount,
    FlowRevision,
    FlowImage,
    ActionSet,
    RuleSet,
)
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.links.models import Link, LinkContacts
from temba.triggers.models import Trigger
from temba.utils import json
from temba.utils.models import TembaModel, generate_uuid

logger = logging.getLogger(__name__)


class MigrationTask(TembaModel):
    STATUS_PENDING = "P"
    STATUS_PROCESSING = "O"
    STATUS_COMPLETE = "C"
    STATUS_FAILED = "F"
    STATUS_CHOICES = (
        (STATUS_PENDING, _("Pending")),
        (STATUS_PROCESSING, _("Processing")),
        (STATUS_COMPLETE, _("Complete")),
        (STATUS_FAILED, _("Failed")),
    )

    org = models.ForeignKey(
        "orgs.Org",
        on_delete=models.PROTECT,
        related_name="%(class)ss",
        help_text=_("The organization of this import progress."),
    )

    migration_org = models.PositiveIntegerField(
        verbose_name=_("Org ID"), help_text=_("The organization ID on live server that is being migrated")
    )

    status = models.CharField(max_length=1, default=STATUS_PENDING, choices=STATUS_CHOICES)

    @classmethod
    def create(cls, org, user, migration_org):
        return cls.objects.create(org=org, migration_org=migration_org, created_by=user, modified_by=user)

    def update_status(self, status):
        self.status = status
        self.save(update_fields=("status", "modified_on"))

    def perform(self):
        self.update_status(self.STATUS_PROCESSING)

        migration_folder = f"{settings.MEDIA_ROOT}/migration_logs"
        if not os.path.exists(migration_folder):
            os.makedirs(migration_folder)

        log_handler = logging.FileHandler(filename=f"{migration_folder}/{self.uuid}.log")
        logger.addHandler(log_handler)
        logger.setLevel("INFO")

        start = timezone.now()

        migrator = Migrator(org_id=self.migration_org)

        try:

            logger.info("---------------- Organization ----------------")
            logger.info("[STARTED] Organization data migration")

            org_data = migrator.get_org()
            if not org_data:
                logger.info("[ERROR] No organization data found")
                self.update_status(self.STATUS_FAILED)
                return

            self.update_org(org_data)
            logger.info("[COMPLETED] Organization data migration")
            logger.info("")

            logger.info("---------------- TopUps ----------------")
            logger.info("[STARTED] TopUps migration")

            # Inactivating all org topups before importing the ones from Live server
            TopUp.objects.filter(is_active=True, org=self.org).update(is_active=False)

            org_topups, topup_count = migrator.get_org_topups()
            if org_topups:
                self.add_topups(logger=logger, topups=org_topups, migrator=migrator, count=topup_count)

            logger.info("[COMPLETED] TopUps migration")
            logger.info("")

            logger.info("---------------- Languages ----------------")
            logger.info("[STARTED] Languages migration")

            # Inactivating all org languages before importing the ones from Live server
            self.org.primary_language = None
            self.org.save(update_fields=["primary_language"])

            Language.objects.filter(is_active=True, org=self.org).delete()

            org_languages, languages_count = migrator.get_org_languages()
            if org_languages:
                self.add_languages(logger=logger, languages=org_languages, count=languages_count)

                if org_data.primary_language_id:
                    org_primary_language = MigrationAssociation.get_new_object(
                        model=MigrationAssociation.MODEL_ORG_LANGUAGE, old_id=org_data.primary_language_id
                    )
                    self.org.primary_language = org_primary_language
                    self.org.save(update_fields=["primary_language"])

            logger.info("[COMPLETED] Languages migration")
            logger.info("")

            logger.info("---------------- Channels ----------------")
            logger.info("[STARTED] Channels migration")

            # Inactivating all org channels before importing the ones from Live server
            existing_channels = Channel.objects.filter(org=self.org)
            for channel in existing_channels:
                channel.uuid = generate_uuid()
                channel.secret = None
                channel.save(update_fields=["uuid", "secret"])
                channel.release(deactivate=False)

            org_channels, channels_count = migrator.get_org_channels()
            if org_channels:
                self.add_channels(logger=logger, channels=org_channels, migrator=migrator, count=channels_count)

            logger.info("[COMPLETED] Channels migration")
            logger.info("")

            logger.info("---------------- Contact Fields ----------------")
            logger.info("[STARTED] Contact Fields migration")

            org_contact_fields, fields_count = migrator.get_org_contact_fields()
            if org_contact_fields:
                self.add_contact_fields(logger=logger, fields=org_contact_fields, count=fields_count)

            logger.info("[COMPLETED] Contact Fields migration")
            logger.info("")

            logger.info("---------------- Contacts ----------------")
            logger.info("[STARTED] Contacts migration")

            org_contacts, contacts_count = migrator.get_org_contacts()
            if org_contacts:
                self.add_contacts(logger=logger, contacts=org_contacts, migrator=migrator, count=contacts_count)

            logger.info("[COMPLETED] Contacts migration")
            logger.info("")

            logger.info("---------------- Contact Groups ----------------")
            logger.info("[STARTED] Contact Groups migration")

            # Releasing current contact groups
            contact_groups = ContactGroup.user_groups.filter(org=self.org).only("id", "uuid").order_by("id")
            for contact_group in contact_groups:
                contact_group.release()
                contact_group.uuid = generate_uuid()
                contact_group.save(update_fields=["uuid"])

            org_contact_groups, contact_groups_count = migrator.get_org_contact_groups()
            if org_contact_groups:
                self.add_contact_groups(
                    logger=logger, groups=org_contact_groups, migrator=migrator, count=contact_groups_count
                )

            logger.info("[COMPLETED] Contact Groups migration")
            logger.info("")

            logger.info("---------------- Channel Events ----------------")
            logger.info("[STARTED] Channel Events migration")

            # Removing all channel events before importing them from live server
            ChannelEvent.objects.filter(org=self.org).delete()

            org_channel_events, channel_events_count = migrator.get_org_channel_events()
            if org_channel_events:
                self.add_channel_events(logger=logger, channel_events=org_channel_events, count=channel_events_count)

            logger.info("[COMPLETED] Channel Events migration")
            logger.info("")

            logger.info("---------------- Schedules ----------------")
            logger.info("[STARTED] Schedules migration")

            # Inactivating all schedules before the migration as we can re-run the script to re-import the schedules
            Schedule.objects.filter(org=self.org, is_active=True).update(is_active=False)

            org_trigger_schedules, trigger_schedules_count = migrator.get_org_trigger_schedules()
            if org_trigger_schedules:
                self.add_schedules(logger=logger, schedules=org_trigger_schedules, count=trigger_schedules_count)

            logger.info("[COMPLETED] Trigger schedules migration")
            logger.info("[STARTED] Broadcast schedules migration")

            org_broadcast_schedules, broadcast_schedules_count = migrator.get_org_broadcast_schedules()
            if org_broadcast_schedules:
                self.add_schedules(logger=logger, schedules=org_broadcast_schedules, count=broadcast_schedules_count)

            logger.info("[COMPLETED] Schedules migration")
            logger.info("")

            logger.info("---------------- Msg Broadcasts ----------------")
            logger.info("[STARTED] Msg Broadcasts migration")

            Broadcast.objects.filter(org=self.org, is_active=True).update(is_active=False)

            org_msg_broadcasts, msg_broadcast_count = migrator.get_org_msg_broadcasts()
            if org_msg_broadcasts:
                self.add_msg_broadcasts(
                    logger=logger, msg_broadcasts=org_msg_broadcasts, migrator=migrator, count=msg_broadcast_count
                )

            logger.info("[COMPLETED] Msg Broadcasts migration")
            logger.info("")

            logger.info("---------------- Msg Labels ----------------")
            logger.info("[STARTED] Msg Labels migration")

            org_msg_folders, folders_count = migrator.get_org_msg_labels(label_type="F")
            if org_msg_folders:
                self.add_msg_folders(logger=logger, folders=org_msg_folders, count=folders_count)

            org_msg_labels, labels_count = migrator.get_org_msg_labels(label_type="L")
            if org_msg_labels:
                self.add_msg_labels(logger=logger, labels=org_msg_labels, count=labels_count)

            logger.info("[COMPLETED] Msg Labels migration")
            logger.info("")

            logger.info("---------------- Msgs ----------------")
            logger.info("[STARTED] Msgs migration")

            all_org_msgs = Msg.objects.filter(org=self.org).only("id").order_by("id")
            for msg in all_org_msgs:
                msg.release(delete_reason=None)

            org_msgs, msgs_count = migrator.get_org_msgs()
            if org_msgs:
                self.add_msgs(logger=logger, msgs=org_msgs, migrator=migrator, count=msgs_count)

            logger.info("[COMPLETED] Msgs migration")
            logger.info("")

            logger.info("---------------- Channel Logs ----------------")
            logger.info("[STARTED] Channel Logs migration")

            if org_channels:
                self.add_channel_logs(logger=logger, channels=org_channels, migrator=migrator)

            logger.info("[COMPLETED] Channel Logs migration")
            logger.info("")

            logger.info("---------------- Flow Labels ----------------")
            logger.info("[STARTED] Flow Labels migration")

            org_flow_labels, flow_labels_count = migrator.get_org_flow_labels()
            if org_flow_labels:
                self.add_flow_labels(logger=logger, labels=org_flow_labels, count=flow_labels_count)

            logger.info("[COMPLETED] Flow Labels migration")
            logger.info("")

            logger.info("---------------- Flows ----------------")
            logger.info("[STARTED] Flows migration")

            org_flows, flows_count = migrator.get_org_flows()
            if org_flows:
                self.add_flows(logger=logger, flows=org_flows, migrator=migrator, count=flows_count)

                self.add_flow_flow_dependencies(flows=org_flows, migrator=migrator)

            logger.info("[COMPLETED] Flows migration")
            logger.info("")

            logger.info("---------------- Campaigns ----------------")
            logger.info("[STARTED] Campaigns migration")

            org_campaigns, campaigns_count = migrator.get_org_campaigns()
            if org_campaigns:
                self.add_campaigns(logger=logger, campaigns=org_campaigns, migrator=migrator, count=campaigns_count)

            logger.info("[COMPLETED] Campaigns migration")
            logger.info("")

            logger.info("---------------- Triggers ----------------")
            logger.info("[STARTED] Triggers migration")

            # Releasing triggers before importing them from live server
            triggers = Trigger.objects.filter(org=self.org, is_active=True)
            for t in triggers:
                t.release()

            org_triggers, triggers_count = migrator.get_org_triggers()
            if org_triggers:
                self.add_triggers(logger=logger, triggers=org_triggers, migrator=migrator, count=triggers_count)

            logger.info("[COMPLETED] Triggers migration")
            logger.info("")

            logger.info("---------------- Trackable Links ----------------")
            logger.info("[STARTED] Trackable Links migration")

            org_links, links_count = migrator.get_org_links()
            if org_links:
                self.add_links(logger=logger, links=org_links, migrator=migrator, count=links_count)

            logger.info("[COMPLETED] Trackable Links migration")
            logger.info("")

            end = timezone.now()

            logger.info(f"Started: {start}")
            logger.info(f"Finished: {end}")

            self.update_status(self.STATUS_COMPLETE)

        except Exception as e:
            logger.error(f"[ERROR] {str(e)}", exc_info=True)
            self.update_status(self.STATUS_FAILED)
        finally:
            self.remove_association()

    def update_org(self, org_data):
        self.org.plan = org_data.plan
        self.org.plan_start = org_data.plan_start
        self.org.stripe_customer = org_data.stripe_customer
        self.org.language = org_data.language
        self.org.date_format = org_data.date_format
        self.org.config = json.loads(org_data.config) if org_data.config else dict()
        self.org.is_anon = org_data.is_anon
        self.org.surveyor_password = org_data.surveyor_password

        if org_data.parent_id:
            new_org_parent_obj = MigrationAssociation.get_new_object(
                model=MigrationAssociation.MODEL_ORG, old_id=org_data.parent_id
            )
            if new_org_parent_obj:
                self.org.parent = new_org_parent_obj

        self.org.save(
            update_fields=[
                "plan",
                "plan_start",
                "stripe_customer",
                "language",
                "date_format",
                "config",
                "is_anon",
                "surveyor_password",
                "parent",
            ]
        )

        MigrationAssociation.create(
            migration_task=self, old_id=org_data.id, new_id=self.org.id, model=MigrationAssociation.MODEL_ORG
        )

    def add_topups(self, logger, topups, migrator, count):
        for idx, topup in enumerate(topups, start=1):
            logger.info(f">>>[{idx}/{count}] TopUp: {topup.id} - {topup.credits}")
            new_topup = TopUp.create(
                user=self.created_by,
                price=topup.price,
                credits=topup.credits,
                org=self.org,
                expires_on=topup.expires_on,
            )
            new_topup.created_on = topup.created_on
            new_topup.modified_on = topup.modified_on
            new_topup.save(update_fields=["created_on", "modified_on"])

            MigrationAssociation.create(
                migration_task=self, old_id=topup.id, new_id=new_topup.id, model=MigrationAssociation.MODEL_ORG_TOPUP
            )

            org_topup_credits = migrator.get_org_topups_credit(topup_id=topup.id)
            for topup_credit in org_topup_credits:
                TopUpCredits.objects.create(
                    topup=new_topup, used=topup_credit.used, is_squashed=topup_credit.is_squashed
                )

    def add_languages(self, logger, languages, count):
        for idx, language in enumerate(languages, start=1):
            logger.info(f">>> [{idx}/{count}] Language: {language.id} - {language.name}")

            new_language = Language.create(
                org=self.org, user=self.created_by, name=language.name, iso_code=language.iso_code
            )

            MigrationAssociation.create(
                migration_task=self,
                old_id=language.id,
                new_id=new_language.id,
                model=MigrationAssociation.MODEL_ORG_LANGUAGE,
            )

    def add_channels(self, logger, channels, migrator, count):
        for idx, channel in enumerate(channels, start=1):
            logger.info(f">>> [{idx}/{count}] Channel: {channel.id} - {channel.name}")

            channel_type = channel.channel_type

            channel_config = json.loads(channel.config) if channel.config else dict()

            if channel_type == "WS":
                # Changing type for WebSocket channel
                channel_type = "WCH"
                if "logo" in channel_config:
                    logo = channel_config.get("logo")
                    file_obj = self.org.get_temporary_file_from_url(media_url=logo)
                    file_extension = logo.split(".")[-1]
                    channel_config["logo"] = self.org.save_media(file=file_obj, extension=file_extension)
            elif channel_type == "FB" and channel.secret:
                # Adding channel secret when it is a Facebook channel to channel config field
                channel_config[Channel.CONFIG_SECRET] = channel.secret

            if isinstance(channel_type, str):
                channel_type = Channel.get_type_from_code(channel_type)

            schemes = channel_type.schemes

            new_channel = Channel.objects.create(
                created_on=channel.created_on,
                modified_on=channel.modified_on,
                org=self.org,
                created_by=self.created_by,
                modified_by=self.created_by,
                country=channel.country,
                channel_type=channel_type.code,
                name=channel.name or channel.address,
                address=channel.address,
                config=channel_config,
                role=channel.role,
                schemes=schemes,
                uuid=channel.uuid,
                claim_code=channel.claim_code,
                secret=channel.secret,
                last_seen=channel.last_seen,
                device=channel.device,
                os=channel.os,
                alert_email=channel.alert_email,
                bod=channel.bod,
                tps=settings.COURIER_DEFAULT_TPS,
            )

            MigrationAssociation.create(
                migration_task=self, old_id=channel.id, new_id=new_channel.id, model=MigrationAssociation.MODEL_CHANNEL
            )

            channel_counts = migrator.get_channels_count(channel_id=channel.id)
            for channel_count in channel_counts:
                ChannelCount.objects.create(
                    channel=new_channel,
                    count_type=channel_count.count_type,
                    day=channel_count.day,
                    count=channel_count.count,
                    is_squashed=channel_count.is_squashed,
                )

            # If the channel is an Android channel it will migrate the sync events
            if channel_type.code == "A":
                channel_syncevents = migrator.get_channel_syncevents(channel_id=channel.id)
                for channel_syncevent in channel_syncevents:
                    SyncEvent.objects.create(
                        created_by=self.created_by,
                        modified_by=self.created_by,
                        channel=new_channel,
                        power_source=channel_syncevent.power_source,
                        power_status=channel_syncevent.power_status,
                        power_level=channel_syncevent.power_level,
                        network_type=channel_syncevent.network_type,
                        lifetime=channel_syncevent.lifetime,
                        pending_message_count=channel_syncevent.pending_message_count,
                        retry_message_count=channel_syncevent.retry_message_count,
                        incoming_command_count=channel_syncevent.incoming_command_count,
                        outgoing_command_count=channel_syncevent.outgoing_command_count,
                    )

    def add_channel_events(self, logger, channel_events, count):
        for idx, event in enumerate(channel_events, start=1):
            logger.info(f">>> [{idx}/{count}] Channel Event: {event.id} - {event.event_type}")

            new_contact_obj = MigrationAssociation.get_new_object(
                model=MigrationAssociation.MODEL_CONTACT, old_id=event.contact_id
            )

            if not new_contact_obj:
                continue

            new_contact_urn_obj = MigrationAssociation.get_new_object(
                model=MigrationAssociation.MODEL_CONTACT_URN, old_id=event.contact_urn_id
            )

            new_channel_obj = MigrationAssociation.get_new_object(
                model=MigrationAssociation.MODEL_CHANNEL, old_id=event.channel_id
            )

            if not new_channel_obj:
                continue

            ChannelEvent.objects.create(
                org=self.org,
                channel=new_channel_obj,
                event_type=event.event_type,
                contact=new_contact_obj,
                contact_urn=new_contact_urn_obj,
                extra=json.loads(event.extra) if event.extra else dict(),
                occurred_on=event.occurred_on,
                created_on=event.created_on,
            )

    def add_channel_logs(self, logger, channels, migrator):
        for channel in channels:
            channel_logs, channel_logs_count = migrator.get_channel_logs(channel_id=channel.id)

            new_channel_obj = MigrationAssociation.get_new_object(
                model=MigrationAssociation.MODEL_CHANNEL, old_id=channel.id
            )

            if not new_channel_obj:
                continue

            # Removing all logs of this channel before importing the new ones to avoid duplicated
            new_channel_obj.logs.all().delete()

            for idx, channel_log in enumerate(channel_logs, start=1):
                description = (
                    f"{channel_log.description[:30]}..."
                    if len(channel_log.description) > 30
                    else channel_log.description
                )
                logger.info(f">>> [{idx}/{channel_logs_count}] Channel Log: {channel_log.id} - {description}")

                new_msg_obj = None
                if channel_log.msg_id:
                    new_msg_obj = MigrationAssociation.get_new_object(
                        model=MigrationAssociation.MODEL_MSG, old_id=channel_log.msg_id
                    )

                new_channel_log = ChannelLog.objects.create(
                    channel=new_channel_obj,
                    msg=new_msg_obj,
                    connection=None,
                    description=channel_log.description,
                    is_error=channel_log.is_error,
                    url=channel_log.url,
                    method=channel_log.method,
                    request=channel_log.request,
                    response=channel_log.response,
                    response_status=channel_log.response_status,
                    request_time=channel_log.request_time,
                )
                new_channel_log.created_on = channel_log.created_on
                new_channel_log.save(update_fields=["created_on"])

    def add_contact_fields(self, logger, fields, count):
        for idx, field in enumerate(fields, start=1):
            logger.info(f">>> [{idx}/{count}] Contact Field: {field.id} - {field.label}")

            new_contact_field = ContactField.get_or_create(
                user=self.created_by,
                org=self.org,
                key=field.key,
                label=field.label,
                show_in_table=field.show_in_table,
                value_type=field.value_type,
            )
            if field.uuid != new_contact_field.uuid:
                new_contact_field.uuid = field.uuid
                new_contact_field.save(update_fields=["uuid"])

            MigrationAssociation.create(
                migration_task=self,
                old_id=field.id,
                new_id=new_contact_field.id,
                model=MigrationAssociation.MODEL_CONTACT_FIELD,
            )

    def add_contacts(self, logger, contacts, migrator, count):
        for idx, contact in enumerate(contacts, start=1):
            logger.info(f">>> [{idx}/{count}] Contact: {contact.uuid} - {contact.name}")

            existing_contact = Contact.objects.filter(uuid=contact.uuid, org=self.org).first()
            if not existing_contact:
                existing_contact = Contact.objects.create(
                    org=self.org,
                    created_by=self.created_by,
                    modified_by=self.created_by,
                    name=contact.name,
                    language=contact.language,
                    is_blocked=contact.is_blocked,
                    is_stopped=contact.is_stopped,
                    is_active=contact.is_active,
                    uuid=contact.uuid,
                )

            # Making sure that the contacts will have the same created_on and modified_on from live
            # If it is not the same from live, would affect the contact messages history
            existing_contact.created_on = contact.created_on
            existing_contact.modified_on = contact.modified_on
            existing_contact.save(update_fields=["created_on", "modified_on"], handle_update=True)

            MigrationAssociation.create(
                migration_task=self,
                old_id=contact.id,
                new_id=existing_contact.id,
                model=MigrationAssociation.MODEL_CONTACT,
            )

            values = migrator.get_values_value(contact_id=contact.id)
            for item in values:
                new_field_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT_FIELD, old_id=item.contact_field_id
                )
                if new_field_obj:
                    existing_contact.set_field(user=self.created_by, key=new_field_obj.key, value=item.string_value)

            urns = migrator.get_contact_urns(contact_id=contact.id)
            for item in urns:
                new_channel_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CHANNEL, old_id=item.channel_id
                )

                identity = str(item.identity)
                if identity.startswith("ws:"):
                    identity = identity.replace("ws:", "ext:")

                new_urn = ContactURN.get_or_create(
                    org=self.org,
                    contact=existing_contact,
                    urn_as_string=identity,
                    channel=new_channel_obj,
                    auth=item.auth,
                )

                MigrationAssociation.create(
                    migration_task=self,
                    old_id=item.id,
                    new_id=new_urn.id,
                    model=MigrationAssociation.MODEL_CONTACT_URN,
                )

    def add_contact_groups(self, logger, groups, migrator, count):
        for idx, group in enumerate(groups, start=1):
            logger.info(f">>> [{idx}/{count}] Contact Group: {group.uuid} - {group.name}")

            contact_group = ContactGroup.get_or_create(
                org=self.org, user=self.created_by, name=group.name, query=group.query, uuid=group.uuid
            )

            # Making sure that the uuid will be the same from live server
            if contact_group.uuid != group.uuid:
                contact_group.uuid = group.uuid
                contact_group.save(update_fields=["uuid"])

            MigrationAssociation.create(
                migration_task=self,
                old_id=group.id,
                new_id=contact_group.id,
                model=MigrationAssociation.MODEL_CONTACT_GROUP,
            )

            contactgroup_contacts = migrator.get_contactgroups_contacts(contactgroup_id=group.id)
            for item in contactgroup_contacts:
                new_contact_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT, old_id=item.contact_id
                )
                if new_contact_obj and not contact_group.is_dynamic:
                    contact_group.update_contacts(user=self.created_by, contacts=[new_contact_obj], add=True)

    def add_schedules(self, logger, schedules, count):
        WEEKDAYS = "MTWRFSU"

        for idx, schedule in enumerate(schedules, start=1):
            logger.info(f">>> [{idx}/{count}] Schedule: {schedule.id}")

            if schedule.repeat_period == "W":
                repeat_days_of_week = ""
                bitmask_number = bin(schedule.repeat_days)
                for idx in range(7):
                    power = bin(pow(2, idx + 1))
                    if bin(int(bitmask_number, 2) & int(power, 2)) == power:
                        repeat_days_of_week += WEEKDAYS[idx]
            else:
                repeat_days_of_week = schedule.repeat_days

            if schedule.repeat_period != "O":
                now = datetime.utcnow().replace(minute=0, second=0, microsecond=0, tzinfo=pytz.utc)
                tz = self.org.timezone
                hour_time = now.replace(hour=schedule.repeat_hour_of_day)
                local_now = tz.normalize(hour_time.astimezone(tz))
                repeat_hour_of_day = local_now.hour
            else:
                repeat_hour_of_day = schedule.repeat_hour_of_day

            new_schedule_obj = Schedule.objects.create(
                created_on=schedule.created_on,
                modified_on=schedule.modified_on,
                created_by=self.created_by,
                modified_by=self.created_by,
                org=self.org,
                repeat_period=schedule.repeat_period,
                repeat_hour_of_day=repeat_hour_of_day,
                repeat_minute_of_hour=schedule.repeat_minute_of_hour or 0,
                repeat_day_of_month=schedule.repeat_day_of_month,
                repeat_days_of_week=repeat_days_of_week,
                next_fire=schedule.next_fire,
                last_fire=schedule.last_fire,
            )

            MigrationAssociation.create(
                migration_task=self,
                old_id=schedule.id,
                new_id=new_schedule_obj.id,
                model=MigrationAssociation.MODEL_SCHEDULE,
            )

    def add_msg_broadcasts(self, logger, msg_broadcasts, migrator, count):
        for idx, broadcast in enumerate(msg_broadcasts, start=1):
            logger.info(f">>> [{idx}/{count}] Msg Broadcast: {broadcast.id}")

            new_channel_obj = (
                MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CHANNEL, old_id=broadcast.channel_id
                )
                if broadcast.channel_id
                else None
            )

            new_schedule_obj = (
                MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_SCHEDULE, old_id=broadcast.schedule_id
                )
                if broadcast.schedule_id
                else None
            )

            new_broadcast_parent_obj = (
                MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_MSG_BROADCAST, old_id=broadcast.parent_id
                )
                if broadcast.parent_id
                else None
            )

            new_broadcast_obj = Broadcast.objects.create(
                org=self.org,
                channel=new_channel_obj,
                status=broadcast.status,
                schedule=new_schedule_obj,
                parent=new_broadcast_parent_obj,
                text=broadcast.text,
                base_language=broadcast.base_language,
                is_active=broadcast.is_active,
                created_by=self.created_by,
                modified_by=self.created_by,
                created_on=broadcast.created_on,
                modified_on=broadcast.modified_on,
                media=broadcast.media,
                send_all=broadcast.send_all,
                metadata=json.loads(broadcast.metadata) if broadcast.metadata else dict(),
            )

            MigrationAssociation.create(
                migration_task=self,
                old_id=broadcast.id,
                new_id=new_broadcast_obj.id,
                model=MigrationAssociation.MODEL_MSG_BROADCAST,
            )

            broadcast_contacts = migrator.get_msg_broadcast_contacts(broadcast_id=broadcast.id)
            for item in broadcast_contacts:
                new_contact_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT, old_id=item.contact_id
                )
                if new_contact_obj:
                    new_broadcast_obj.contacts.add(new_contact_obj)

            broadcast_groups = migrator.get_msg_broadcast_groups(broadcast_id=broadcast.id)
            for item in broadcast_groups:
                new_group_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT_GROUP, old_id=item.contactgroup_id
                )
                if new_group_obj:
                    new_broadcast_obj.groups.add(new_group_obj)

            broadcast_urns = migrator.get_msg_broadcast_urns(broadcast_id=broadcast.id)
            for item in broadcast_urns:
                new_urn_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT_URN, old_id=item.contacturn_id
                )
                if new_urn_obj:
                    new_broadcast_obj.urns.add(new_urn_obj)

    def add_msg_folders(self, logger, folders, count):
        for idx, folder in enumerate(folders, start=1):
            logger.info(f">>> [{idx}/{count}] Msg Folder: {folder.uuid} - {folder.name}")

            new_msg_folder = Label.get_or_create_folder(org=self.org, user=self.created_by, name=folder.name)
            if folder.uuid != new_msg_folder.uuid:
                new_msg_folder.uuid = folder.uuid
                new_msg_folder.save(update_fields=["uuid"])

            MigrationAssociation.create(
                migration_task=self,
                old_id=folder.id,
                new_id=new_msg_folder.id,
                model=MigrationAssociation.MODEL_MSG_LABEL,
            )

    def add_msg_labels(self, logger, labels, count):
        for idx, label in enumerate(labels, start=1):
            logger.info(f">>> [{idx}/{count}] Msg Label: {label.uuid} - {label.name}")

            new_msg_label = Label.get_or_create(org=self.org, user=self.created_by, name=label.name)

            if label.uuid != new_msg_label.uuid:
                new_msg_label.uuid = label.uuid
                new_msg_label.save(update_fields=["uuid"])

            if label.folder_id:
                new_folder_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_MSG_LABEL, old_id=label.folder_id
                )
                new_msg_label.folder = new_folder_obj
                new_msg_label.save(update_fields=["folder"])

            MigrationAssociation.create(
                migration_task=self,
                old_id=label.id,
                new_id=new_msg_label.id,
                model=MigrationAssociation.MODEL_MSG_LABEL,
            )

    def add_msgs(self, logger, msgs, migrator, count):
        for idx, msg in enumerate(msgs, start=1):
            msg_text = f"{msg.text[:30]}..." if len(msg.text) > 30 else msg.text
            msg_direction = "Incoming" if msg.direction == "I" else "Outgoing"

            logger.info(f">>> [{idx}/{count}] Msg: {msg.uuid} - [{msg_direction}] {msg_text}")

            response_to = None
            if msg.response_to_id:
                new_msg_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_MSG, old_id=msg.response_to_id
                )
                response_to = new_msg_obj

            new_channel_obj = None
            if msg.channel_id:
                new_channel_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CHANNEL, old_id=msg.channel_id
                )

            new_contact_obj = MigrationAssociation.get_new_object(
                model=MigrationAssociation.MODEL_CONTACT, old_id=msg.contact_id
            )

            if not new_contact_obj:
                continue

            new_contact_urn_obj = None
            if msg.contact_urn_id:
                new_contact_urn_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT_URN, old_id=msg.contact_urn_id
                )

            new_broadcast_obj = None
            if msg.broadcast_id:
                new_broadcast_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_MSG_BROADCAST, old_id=msg.broadcast_id
                )

            new_topup_obj = None
            if msg.topup_id:
                new_topup_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_ORG_TOPUP, old_id=msg.topup_id
                )

            attachments = msg.attachments
            if attachments:
                for idx, item in enumerate(attachments):
                    [content_type, url] = item.split(":", 1)

                    if (
                        content_type in ["image", "audio", "video", "geo"]
                        or "amazonaws.com" in url
                        or not settings.AWS_S3_ENABLED
                    ):
                        continue

                    if "demo.citizeninsights.org" in url:
                        url = url.replace("https://demo.citizeninsights.org", settings.MIGRATION_FROM_URL)

                    # We only migrate files that can be accessible via HTTP request
                    try:
                        file_obj = self.org.get_temporary_file_from_url(media_url=url)
                        file_extension = url.split(".")[-1] if url else None
                        s3_file_url = self.org.save_media(file=file_obj, extension=file_extension)
                        attachments[idx] = f"{content_type}:{s3_file_url}"
                    except Exception as e:
                        logger.warning(f"Image was not UPLOADED to S3: {url}. Reason: {str(e)}")
                        pass

            new_msg = Msg.objects.create(
                uuid=msg.uuid,
                org=self.org,
                channel=new_channel_obj,
                contact=new_contact_obj,
                contact_urn=new_contact_urn_obj,
                broadcast=new_broadcast_obj,
                text=msg.text,
                high_priority=msg.high_priority,
                created_on=msg.created_on,
                modified_on=msg.modified_on,
                sent_on=msg.sent_on,
                queued_on=msg.queued_on,
                direction=msg.direction,
                status=msg.status,
                response_to=response_to,
                visibility=msg.visibility,
                msg_type=msg.msg_type,
                msg_count=msg.msg_count,
                error_count=msg.error_count,
                next_attempt=msg.next_attempt,
                external_id=msg.external_id,
                topup=new_topup_obj,
                attachments=attachments,
                metadata=json.loads(msg.metadata) if msg.metadata else dict(),
            )

            if new_msg.uuid != msg.uuid:
                new_msg.uuid = msg.uuid
                new_msg.save(update_fields=["uuid"])

            MigrationAssociation.create(
                migration_task=self, old_id=msg.id, new_id=new_msg.id, model=MigrationAssociation.MODEL_MSG
            )

            msg_labels = migrator.get_msg_labels(msg_id=msg.id)
            for item in msg_labels:
                new_label_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_MSG_LABEL, old_id=item.label_id
                )
                if new_label_obj:
                    new_msg.labels.add(new_label_obj)

    def add_flow_labels(self, logger, labels, count):
        for idx, label in enumerate(labels, start=1):
            logger.info(f">>> [{idx}/{count}] Flow Label: {label.uuid} - {label.name}")

            new_flow_label = FlowLabel.objects.filter(uuid=label.uuid).only("id").first()
            if not new_flow_label:
                new_flow_label = FlowLabel.objects.create(org=self.org, uuid=label.uuid, name=label.name)

            if new_flow_label.uuid != label.uuid:
                new_flow_label.uuid = label.uuid
                new_flow_label.save(update_fields=["uuid"])

            if label.parent_id:
                new_flow_label_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_FLOW_LABEL, old_id=label.parent_id
                )
                new_flow_label.parent = new_flow_label_obj
                new_flow_label.save(update_fields=["parent"])

            MigrationAssociation.create(
                migration_task=self,
                old_id=label.id,
                new_id=new_flow_label.id,
                model=MigrationAssociation.MODEL_FLOW_LABEL,
            )

    def add_flows(self, logger, flows, migrator, count):
        for idx, flow in enumerate(flows, start=1):
            logger.info(f">>> [{idx}/{count}] Flow: {flow.uuid} - {flow.name}")

            if flow.flow_type == "U":
                logger.info(f">>> This flow was skipped because it is an USSD flow type: {flow.name}")
                continue

            new_flow = Flow.objects.filter(uuid=flow.uuid).only("id").first()
            if not new_flow:
                metadata = dict()

                if flow.metadata:
                    metadata = json.loads(flow.metadata)
                    dependencies = metadata.get("dependencies", {})
                    if isinstance(dependencies, dict):
                        new_deps = []
                        for key, deps_for_key in dependencies.items():
                            type_name = key[:-1]
                            for dep in deps_for_key:
                                dep["type"] = type_name
                                new_deps.append(dep)

                        new_deps = sorted(new_deps, key=lambda d: d["type"])
                        metadata["dependencies"] = new_deps
                    if "results" not in metadata:
                        metadata["results"] = []
                    if "waiting_exit_uuids" not in metadata:
                        metadata["waiting_exit_uuids"] = []

                new_flow = Flow.create(
                    org=self.org,
                    user=self.created_by,
                    name=flow.name,
                    flow_type="M" if flow.flow_type in ["F", "M"] else flow.flow_type,
                    expires_after_minutes=flow.expires_after_minutes,
                    base_language=flow.base_language,
                    is_system=flow.flow_type == "M",
                    uuid=flow.uuid,
                    entry_uuid=flow.entry_uuid,
                    entry_type=flow.entry_type,
                    is_archived=flow.is_archived,
                    metadata=metadata,
                    ignore_triggers=flow.ignore_triggers,
                )
                new_flow.saved_on = flow.saved_on
                new_flow.created_on = flow.created_on
                new_flow.modified_on = flow.modified_on
                new_flow.save(update_fields=["saved_on", "created_on", "modified_on"])

            if new_flow.uuid != flow.uuid:
                new_flow.uuid = flow.uuid
                new_flow.save(update_fields=["uuid"])

            if new_flow.metadata:
                if "results" not in new_flow.metadata:
                    new_flow.metadata["results"] = []

                if "waiting_exit_uuids" not in new_flow.metadata:
                    new_flow.metadata["waiting_exit_uuids"] = []

                new_flow.save(update_fields=["metadata"])

            MigrationAssociation.create(
                migration_task=self, old_id=flow.id, new_id=new_flow.id, model=MigrationAssociation.MODEL_FLOW
            )

            # Removing field dependencies before importing again
            new_flow.field_dependencies.clear()

            field_dependencies = migrator.get_flow_fields_dependencies(flow_id=flow.id)
            for item in field_dependencies:
                new_field_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT_FIELD, old_id=item.contactfield_id
                )
                if new_field_obj:
                    new_flow.field_dependencies.add(new_field_obj)

            # Removing group dependencies before importing again
            new_flow.group_dependencies.clear()

            group_dependencies = migrator.get_flow_group_dependencies(flow_id=flow.id)
            for item in group_dependencies:
                new_group_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT_GROUP, old_id=item.contactgroup_id
                )
                if new_group_obj:
                    new_flow.group_dependencies.add(new_group_obj)

            flow_labels = migrator.get_flow_labels(flow_id=flow.id)
            for item in flow_labels:
                new_label_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_FLOW_LABEL, old_id=item.flowlabel_id
                )
                if new_label_obj:
                    new_flow.labels.add(new_label_obj)

            # Removing flow category count relationships before importing again
            new_flow.category_counts.all().delete()

            category_count = migrator.get_flow_category_count(flow_id=flow.id)
            for item in category_count:
                FlowCategoryCount.objects.create(
                    flow=new_flow,
                    node_uuid=item.node_uuid,
                    result_key=item.result_key,
                    result_name=item.result_name,
                    category_name=item.category_name,
                    count=item.count,
                )

            # Removing flow node count relationships before importing again
            new_flow.node_counts.all().delete()

            node_count = migrator.get_flow_node_count(flow_id=flow.id)
            for item in node_count:
                FlowNodeCount.objects.create(flow=new_flow, node_uuid=item.node_uuid, count=item.count)

            # Removing flow path count relationships before importing again
            new_flow.path_counts.all().delete()

            path_count = migrator.get_flow_path_count(flow_id=flow.id)
            for item in path_count:
                FlowPathCount.objects.create(
                    flow=new_flow,
                    from_uuid=item.from_uuid,
                    to_uuid=item.to_uuid,
                    period=item.period,
                    count=item.count,
                    is_squashed=item.is_squashed,
                )

            # Removing flow actionsets before importing again
            new_flow.action_sets.all().delete()

            logger.info(f">>> Flow ActionSets")
            action_sets = migrator.get_flow_actionsets(flow_id=flow.id)

            for item in action_sets:
                ActionSet.objects.create(
                    uuid=item.uuid,
                    flow=new_flow,
                    destination=item.destination,
                    destination_type=item.destination_type,
                    exit_uuid=item.exit_uuid,
                    actions=json.loads(item.actions) if item.actions else dict(),
                    x=item.x,
                    y=item.y,
                    created_on=item.created_on,
                    modified_on=item.modified_on,
                )

            # Removing flow rulesets before importing again
            new_flow.rule_sets.all().delete()

            logger.info(f">>> Flow RuleSets")
            rule_sets = migrator.get_flow_rulesets(flow_id=flow.id)

            for item in rule_sets:
                RuleSet.objects.create(
                    uuid=item.uuid,
                    flow=new_flow,
                    label=item.label,
                    operand=item.operand,
                    webhook_url=item.webhook_url,
                    webhook_action=item.webhook_action,
                    rules=json.loads(item.rules) if item.rules else dict(),
                    finished_key=item.finished_key,
                    value_type=item.value_type,
                    ruleset_type=item.ruleset_type,
                    response_type=item.response_type,
                    config=json.loads(item.config) if item.config else dict(),
                    x=item.x,
                    y=item.y,
                    created_on=item.created_on,
                    modified_on=item.modified_on,
                )

            # Removing flow revisions before importing again
            new_flow.revisions.all().delete()

            logger.info(f">>> Flow Revisions")
            revisions = migrator.get_flow_revisions(flow_id=flow.id)

            revision_json_dict = {
                Flow.DEFINITION_NAME: flow.name,
                Flow.DEFINITION_UUID: flow.uuid,
                Flow.DEFINITION_SPEC_VERSION: Flow.CURRENT_SPEC_VERSION,
                Flow.DEFINITION_LANGUAGE: flow.base_language,
                Flow.DEFINITION_TYPE: Flow.GOFLOW_TYPES["M" if flow.flow_type in ["F", "M"] else flow.flow_type],
                Flow.DEFINITION_NODES: [],
                Flow.DEFINITION_UI: {},
            }

            if revisions:
                for item in revisions:
                    json_flow = dict()
                    spec_version = item.spec_version
                    if item.definition:
                        try:
                            json_flow = FlowRevision.migrate_definition(
                                json_flow=json.loads(item.definition), flow=new_flow
                            )
                            json_flow = FlowRevision.migrate_issues(json_flow)
                            spec_version = Flow.CURRENT_SPEC_VERSION
                        except Exception:
                            json_flow = json.loads(item.definition)

                    FlowRevision.objects.create(
                        flow=new_flow,
                        definition=json_flow,
                        spec_version=spec_version,
                        revision=item.revision,
                        created_by=self.created_by,
                        modified_by=self.created_by,
                        created_on=item.created_on,
                        modified_on=item.modified_on,
                    )
                    revision_json_dict = json_flow
            else:
                new_flow.save_revision(
                    self.created_by,
                    revision_json_dict,
                )

            # Updating metadata and dependencies
            try:
                flow_info = mailroom.get_client().flow_inspect(self.org.id, revision_json_dict)
                dependencies = flow_info[Flow.INSPECT_DEPENDENCIES]

                new_flow.metadata = Flow.get_metadata(flow_info)
                new_flow.save(update_fields=["metadata"])

                new_flow.update_dependencies(dependencies)
            except Exception as e:
                pass

            # Removing flow images before importing again
            new_flow.flow_images.all().delete()

            flow_images = migrator.get_flow_images(flow_id=flow.id)
            logger.info(f">>> Flow Images")

            for item in flow_images:
                new_contact_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT, old_id=item.contact_id
                )

                if not new_contact_obj:
                    continue

                path_thumbnail_s3_file_url = None
                if settings.AWS_S3_ENABLED:
                    file_path = f"{settings.MIGRATION_FROM_URL}/media/{item.path}"
                    file_path_thumbnail = f"{settings.MIGRATION_FROM_URL}{item.path_thumbnail}"

                    try:
                        file_obj_path = self.org.get_temporary_file_from_url(media_url=file_path)
                        path_s3_file_url = self.org.save_media(file=file_obj_path, extension="jpg")

                        if item.path_thumbnail:
                            file_obj_path_thumbnail = self.org.get_temporary_file_from_url(media_url=file_path_thumbnail)
                            path_thumbnail_s3_file_url = self.org.save_media(file=file_obj_path_thumbnail, extension="jpg")

                    except Exception as e:
                        path_s3_file_url = file_path
                        logger.warning(f"Image was not UPLOADED to S3: {file_path_thumbnail}. Reason: {str(e)}")

                else:
                    path_s3_file_url = item.path
                    path_thumbnail_s3_file_url = item.path_thumbnail

                FlowImage.objects.create(
                    uuid=item.uuid,
                    org=self.org,
                    flow=new_flow,
                    contact=new_contact_obj,
                    name=item.name,
                    path=path_s3_file_url,
                    path_thumbnail=path_thumbnail_s3_file_url,
                    exif=item.exif,
                    created_on=item.created_on,
                    modified_on=item.modified_on,
                    is_active=item.is_active,
                )

            # Releasing flow starts before the migration
            for fs in new_flow.starts.all():
                fs.release()

            logger.info(f">>> Flow Starts")
            flow_starts = migrator.get_flow_starts(flow_id=flow.id)

            for item in flow_starts:
                new_flow_start = FlowStart.objects.create(
                    uuid=item.uuid,
                    flow=new_flow,
                    restart_participants=item.restart_participants,
                    include_active=item.include_active,
                    status=item.status,
                    extra=json.loads(item.extra) if item.extra else dict(),
                    created_by=self.created_by,
                    created_on=item.created_on,
                    is_active=item.is_active,
                    modified_by=self.created_by,
                    modified_on=item.modified_on,
                    contact_count=item.contact_count,
                )

                MigrationAssociation.create(
                    migration_task=self,
                    old_id=item.id,
                    new_id=new_flow_start.id,
                    model=MigrationAssociation.MODEL_FLOW_START,
                )

                flow_start_contacts = migrator.get_flow_start_contacts(flowstart_id=item.id)
                for fsc in flow_start_contacts:
                    new_contact_obj = MigrationAssociation.get_new_object(
                        model=MigrationAssociation.MODEL_CONTACT, old_id=fsc.contact_id
                    )
                    if new_contact_obj:
                        new_flow_start.contacts.add(new_contact_obj)

                flow_start_groups = migrator.get_flow_start_groups(flowstart_id=item.id)
                for fsg in flow_start_groups:
                    new_group_obj = MigrationAssociation.get_new_object(
                        model=MigrationAssociation.MODEL_CONTACT_GROUP, old_id=fsg.contactgroup_id
                    )
                    if new_group_obj:
                        new_flow_start.groups.add(new_group_obj)

            # Releasing flow runs before the migration
            for fr in new_flow.runs.all():
                fr.release()

            logger.info(f">>> Flow Runs")
            flow_runs = migrator.get_flow_runs(flow_id=flow.id)
            for item in flow_runs:
                new_contact_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT, old_id=item.contact_id
                )

                if not new_contact_obj:
                    continue

                new_start_obj = None
                if item.start_id:
                    new_start_obj = MigrationAssociation.get_new_object(
                        model=MigrationAssociation.MODEL_FLOW_START, old_id=item.start_id
                    )

                new_parent_obj = None
                if item.parent_id:
                    new_parent_obj = MigrationAssociation.get_new_object(
                        model=MigrationAssociation.MODEL_FLOW_RUN, old_id=item.parent_id
                    )

                run_path = dict()
                if item.path:
                    run_path = json.loads(item.path)
                    for rp in run_path:
                        if "uuid" not in rp:
                            rp["uuid"] = generate_uuid()
                        if "exit_uuid" not in rp:
                            rp["exit_uuid"] = generate_uuid()
                        if "node_uuid" not in rp:
                            rp["node_uuid"] = generate_uuid()

                new_flow_run = FlowRun.objects.create(
                    uuid=item.uuid,
                    org=self.org,
                    flow=new_flow,
                    contact=new_contact_obj,
                    status=MigrationTask.get_run_status(exit_type=item.exit_type, is_active=item.is_active),
                    created_on=item.created_on,
                    modified_on=item.modified_on,
                    exited_on=item.exited_on,
                    expires_on=item.expires_on,
                    timeout_on=item.timeout_on,
                    responded=item.responded,
                    start=new_start_obj,
                    submitted_by=self.created_by if item.submitted_by_id else None,
                    parent=new_parent_obj,
                    parent_uuid=new_parent_obj.uuid if new_parent_obj else None,
                    results=json.loads(item.results) if item.results else dict(),
                    path=run_path,
                    is_active=item.is_active,
                    exit_type=item.exit_type,
                )

                MigrationAssociation.create(
                    migration_task=self,
                    old_id=item.id,
                    new_id=new_flow_run.id,
                    model=MigrationAssociation.MODEL_FLOW_RUN,
                )

    def add_flow_flow_dependencies(self, flows, migrator):
        for flow in flows:
            new_flow_obj = MigrationAssociation.get_new_object(model=MigrationAssociation.MODEL_FLOW, old_id=flow.id)
            flow_dependencies = migrator.get_flow_flow_dependencies(flow_id=flow.id)
            for item in flow_dependencies:
                new_to_flow_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_FLOW, old_id=item.to_flow_id
                )
                if new_flow_obj and new_to_flow_obj:
                    new_flow_obj.flow_dependencies.add(new_to_flow_obj)

    def add_campaigns(self, logger, campaigns, migrator, count):
        for idx, campaign in enumerate(campaigns, start=1):
            logger.info(f">>> [{idx}/{count}] Campaign: {campaign.uuid} - {campaign.name}")

            new_campaign = Campaign.objects.filter(uuid=campaign.uuid, org=self.org).only("id").first()
            if not new_campaign:
                new_group_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT_GROUP, old_id=campaign.group_id
                )

                if not new_group_obj:
                    continue

                new_campaign = Campaign.objects.create(
                    org=self.org,
                    uuid=campaign.uuid,
                    name=campaign.name,
                    group=new_group_obj,
                    created_on=campaign.created_on,
                    modified_on=campaign.modified_on,
                    created_by=self.created_by,
                    modified_by=self.created_by,
                )

            if new_campaign.uuid != campaign.uuid:
                new_campaign.uuid = campaign.uuid
                new_campaign.save(update_fields=["uuid"])

            if not new_campaign.is_active:
                new_campaign.is_active = True
                new_campaign.save(update_fields=["is_active"])

            if new_campaign.is_archived:
                new_campaign.is_archived = False
                new_campaign.save(update_fields=["is_archived"])

            MigrationAssociation.create(
                migration_task=self,
                old_id=campaign.id,
                new_id=new_campaign.id,
                model=MigrationAssociation.MODEL_CAMPAIGN,
            )

            campaign_events = migrator.get_campaign_events(campaign_id=campaign.id)
            for campaign_event in campaign_events:
                new_contact_field_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT_FIELD, old_id=campaign_event.relative_to_id
                )

                new_flow_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_FLOW, old_id=campaign_event.flow_id
                )

                if not new_contact_field_obj or not new_flow_obj:
                    continue

                new_campaign_event = (
                    CampaignEvent.objects.filter(uuid=campaign_event.uuid, campaign=new_campaign).only("id").first()
                )
                if not new_campaign_event:
                    new_campaign_event = CampaignEvent.objects.create(
                        uuid=campaign_event.uuid,
                        campaign=new_campaign,
                        event_type=campaign_event.event_type,
                        relative_to=new_contact_field_obj,
                        offset=campaign_event.offset,
                        unit=campaign_event.unit,
                        flow=new_flow_obj,
                        message=MigrationTask.migrate_translations(campaign_event.message)
                        if campaign_event.message
                        else None,
                        delivery_hour=campaign_event.delivery_hour,
                        extra=json.loads(campaign_event.embedded_data) if campaign_event.embedded_data else dict(),
                        modified_by=self.created_by,
                        created_by=self.created_by,
                        created_on=campaign_event.created_on,
                        modified_on=campaign_event.modified_on,
                        is_active=campaign_event.is_active,
                    )

                if new_campaign_event.uuid != campaign_event.uuid:
                    new_campaign_event.uuid = campaign_event.uuid
                    new_campaign_event.save(update_fields=["uuid"])

                event_fires = migrator.get_event_fires(event_id=campaign_event.id)
                for event_fire in event_fires:
                    new_contact_obj = MigrationAssociation.get_new_object(
                        model=MigrationAssociation.MODEL_CONTACT, old_id=event_fire.contact_id
                    )

                    if not new_contact_obj:
                        continue

                    EventFire.objects.create(
                        event=new_campaign_event,
                        contact=new_contact_obj,
                        scheduled=event_fire.scheduled,
                        fired=event_fire.fired,
                    )

    def add_triggers(self, logger, triggers, migrator, count):
        for idx, trigger in enumerate(triggers, start=1):
            logger.info(f">>> [{idx}/{count}] Trigger: {trigger.id}")

            new_flow_obj = MigrationAssociation.get_new_object(
                model=MigrationAssociation.MODEL_FLOW, old_id=trigger.flow_id
            )

            if not new_flow_obj:
                continue

            new_schedule_obj = None
            if trigger.schedule_id:
                new_schedule_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_SCHEDULE, old_id=trigger.schedule_id
                )

            new_channel_obj = None
            if trigger.channel_id:
                new_channel_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CHANNEL, old_id=trigger.channel_id
                )

            new_trigger = Trigger.objects.create(
                org=self.org,
                trigger_type=trigger.trigger_type,
                keyword=trigger.keyword,
                referrer_id=trigger.referrer_id,
                flow=new_flow_obj,
                schedule=new_schedule_obj,
                match_type=trigger.match_type,
                channel=new_channel_obj,
                extra=json.loads(trigger.embedded_data) if trigger.embedded_data else dict(),
                created_by=self.created_by,
                modified_by=self.created_by,
                created_on=trigger.created_on,
                modified_on=trigger.modified_on,
            )

            MigrationAssociation.create(
                migration_task=self, old_id=trigger.id, new_id=new_trigger.id, model=MigrationAssociation.MODEL_TRIGGER
            )

            trigger_contacts = migrator.get_trigger_contacts(trigger_id=trigger.id)
            for item in trigger_contacts:
                new_contact_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT, old_id=item.contact_id
                )
                if new_contact_obj:
                    new_trigger.contacts.add(new_contact_obj)

            trigger_groups = migrator.get_trigger_groups(trigger_id=trigger.id)
            for item in trigger_groups:
                new_group_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT_GROUP, old_id=item.contactgroup_id
                )
                if new_group_obj:
                    new_trigger.groups.add(new_group_obj)

    def add_links(self, logger, links, migrator, count):
        for idx, link in enumerate(links, start=1):
            logger.info(f">>> [{idx}/{count}] Link: {link.uuid} - {link.name}")

            new_link = Link.objects.filter(uuid=link.uuid, org=self.org).only("id").first()
            if not new_link:
                new_link = Link.objects.create(
                    org=self.org,
                    name=link.name,
                    destination=link.destination,
                    clicks_count=link.clicks_count,
                    created_by=self.created_by,
                    modified_by=self.created_by,
                    created_on=link.created_on,
                    modified_on=link.modified_on,
                )

            link_contacts = migrator.get_link_contacts(link_id=link.id)
            for item in link_contacts:
                new_contact_obj = MigrationAssociation.get_new_object(
                    model=MigrationAssociation.MODEL_CONTACT, old_id=item.contact_id
                )

                if not new_contact_obj:
                    continue

                LinkContacts.objects.create(
                    link=new_link,
                    contact=new_contact_obj,
                    created_by=self.created_by,
                    modified_by=self.created_by,
                    created_on=item.created_on,
                    modified_on=item.modified_on,
                )

    def remove_association(self):
        return self.associations.all().exclude(model=MigrationAssociation.MODEL_ORG).delete()

    @classmethod
    def get_run_status(cls, exit_type, is_active):
        # Based on 0214_populate_run_status migration file

        exit_type_dict = {
            FlowRun.EXIT_TYPE_COMPLETED: FlowRun.EXIT_TYPE_COMPLETED,
            FlowRun.EXIT_TYPE_INTERRUPTED: FlowRun.EXIT_TYPE_INTERRUPTED,
            FlowRun.STATUS_EXPIRED: FlowRun.STATUS_EXPIRED,
        }
        status = exit_type_dict.get(exit_type, FlowRun.STATUS_COMPLETED)

        if is_active:
            status = FlowRun.STATUS_ACTIVE
        elif not exit_type:
            status = FlowRun.STATUS_INTERRUPTED

        return status

    @classmethod
    def migrate_translations(cls, translations):  # pragma: no cover
        return {lang: mailroom.get_client().expression_migrate(s) for lang, s in translations.items()}


class MigrationAssociation(models.Model):
    MODEL_CAMPAIGN = "campaigns_campaign"
    MODEL_CAMPAIGN_EVENT = "campaigns_campaignevent"
    MODEL_CHANNEL = "channels_channel"
    MODEL_CONTACT = "contacts_contact"
    MODEL_CONTACT_URN = "contacts_contacturn"
    MODEL_CONTACT_GROUP = "contacts_contactgroup"
    MODEL_CONTACT_FIELD = "contacts_contactfield"
    MODEL_MSG = "msgs_msg"
    MODEL_MSG_LABEL = "msgs_label"
    MODEL_MSG_BROADCAST = "msgs_broadcast"
    MODEL_FLOW = "flows_flow"
    MODEL_FLOW_LABEL = "flows_flowlabel"
    MODEL_FLOW_RUN = "flows_flowrun"
    MODEL_FLOW_START = "flows_flowstart"
    MODEL_LINK = "links_link"
    MODEL_SCHEDULE = "schedules_schedule"
    MODEL_ORG = "orgs_org"
    MODEL_ORG_TOPUP = "orgs_topups"
    MODEL_ORG_LANGUAGE = "orgs_language"
    MODEL_TRIGGER = "triggers_trigger"

    MODEL_CHOICES = (
        (MODEL_CAMPAIGN, MODEL_CAMPAIGN),
        (MODEL_CAMPAIGN_EVENT, MODEL_CAMPAIGN_EVENT),
        (MODEL_CHANNEL, MODEL_CHANNEL),
        (MODEL_CONTACT, MODEL_CONTACT),
        (MODEL_CONTACT_URN, MODEL_CONTACT_URN),
        (MODEL_CONTACT_GROUP, MODEL_CONTACT_GROUP),
        (MODEL_CONTACT_FIELD, MODEL_CONTACT_FIELD),
        (MODEL_MSG, MODEL_MSG),
        (MODEL_MSG_LABEL, MODEL_MSG_LABEL),
        (MODEL_MSG_BROADCAST, MODEL_MSG_BROADCAST),
        (MODEL_FLOW, MODEL_FLOW),
        (MODEL_FLOW_LABEL, MODEL_FLOW_LABEL),
        (MODEL_FLOW_RUN, MODEL_FLOW_RUN),
        (MODEL_FLOW_START, MODEL_FLOW_START),
        (MODEL_LINK, MODEL_LINK),
        (MODEL_SCHEDULE, MODEL_SCHEDULE),
        (MODEL_ORG, MODEL_ORG),
        (MODEL_ORG_TOPUP, MODEL_ORG_TOPUP),
        (MODEL_ORG_LANGUAGE, MODEL_ORG_LANGUAGE),
        (MODEL_TRIGGER, MODEL_TRIGGER),
    )

    migration_task = models.ForeignKey(MigrationTask, on_delete=models.CASCADE, related_name="associations")

    old_id = models.PositiveIntegerField(verbose_name=_("The ID provided from live server"))

    new_id = models.PositiveIntegerField(verbose_name=_("The new ID generated on app server"))

    model = models.CharField(verbose_name=_("Model related to the ID"), max_length=255, choices=MODEL_CHOICES)

    def __str__(self):
        return self.model

    @classmethod
    def create(cls, migration_task, old_id, new_id, model):
        return MigrationAssociation.objects.create(
            migration_task=migration_task, old_id=old_id, new_id=new_id, model=model
        )

    @classmethod
    def get_new_object(cls, model, old_id):
        obj = (
            MigrationAssociation.objects.filter(old_id=old_id, model=model)
            .only("new_id", "migration_task__org")
            .select_related("migration_task")
            .order_by("-id")
            .first()
        )

        if not obj:
            return None

        _model = obj.get_related_model()
        if not _model:
            logger.error("[ERROR] No model class found on get_new_object method")
            return None

        if model == MigrationAssociation.MODEL_CONTACT_GROUP:
            queryset = _model.user_groups.filter(id=obj.new_id, org=obj.migration_task.org).first()
        elif model == MigrationAssociation.MODEL_CONTACT_FIELD:
            queryset = _model.user_fields.filter(id=obj.new_id, org=obj.migration_task.org).first()
        elif model == MigrationAssociation.MODEL_MSG_LABEL:
            queryset = _model.all_objects.filter(id=obj.new_id, org=obj.migration_task.org).first()
        elif model in [MigrationAssociation.MODEL_ORG, MigrationAssociation.MODEL_FLOW_START]:
            queryset = _model.objects.filter(id=obj.new_id).first()
        else:
            queryset = _model.objects.filter(id=obj.new_id, org=obj.migration_task.org).first()

        return queryset

    def get_related_model(self):
        model_class = {
            MigrationAssociation.MODEL_CAMPAIGN: Campaign,
            MigrationAssociation.MODEL_CAMPAIGN_EVENT: CampaignEvent,
            MigrationAssociation.MODEL_CHANNEL: Channel,
            MigrationAssociation.MODEL_CONTACT: Contact,
            MigrationAssociation.MODEL_CONTACT_URN: ContactURN,
            MigrationAssociation.MODEL_CONTACT_GROUP: ContactGroup,
            MigrationAssociation.MODEL_CONTACT_FIELD: ContactField,
            MigrationAssociation.MODEL_MSG: Msg,
            MigrationAssociation.MODEL_MSG_LABEL: Label,
            MigrationAssociation.MODEL_MSG_BROADCAST: Broadcast,
            MigrationAssociation.MODEL_FLOW: Flow,
            MigrationAssociation.MODEL_FLOW_LABEL: FlowLabel,
            MigrationAssociation.MODEL_FLOW_RUN: FlowRun,
            MigrationAssociation.MODEL_FLOW_START: FlowStart,
            MigrationAssociation.MODEL_LINK: Link,
            MigrationAssociation.MODEL_SCHEDULE: Schedule,
            MigrationAssociation.MODEL_ORG: Org,
            MigrationAssociation.MODEL_ORG_TOPUP: TopUp,
            MigrationAssociation.MODEL_ORG_LANGUAGE: Language,
            MigrationAssociation.MODEL_TRIGGER: Trigger,
        }
        return model_class.get(self.model, None)
