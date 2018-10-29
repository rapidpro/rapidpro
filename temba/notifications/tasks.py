import logging
import time

import iso8601

from django.utils import timezone

from celery.task import task
from .models import FlowRunCount, FlowNodeCount, FlowPathCount, FlowCategoryCount, FlowPathRecentRun, FlowLabel



@task(track_started=True, name='migrate_flow_to_production')
def migrate_flow_to_production(notification):
    from .models import Notification
    if item.auto_migrated:
        item.mark_automigrated()
    else:
        item.mark_migrated()
    org_dest = notification.org_dest
    name = notification.item_name
    id_item = notification.item_id
    history = notification.history
    flow_dest = Flow.objects.filter(name = name,
                                    org = org_dest).first()
    flow_orig = Flow.objects.filter(id = id_item).first()
    if (notification.accepted or notification.auto_migrated) and history:
        org_dest.import_app({'flows':[history.definition]},
                            notification.created_by)
        if flow_orig and flow_dest:
            flow_dest.restore()
            flow_dest.labels.all().delete()
            #Now migrate the labels
            #Get or create origin_org label
            parent_label = FlowLabel.objects.filter(name = org_dest.name,
                                                    org = org_dest).first()
            if not parent_label:
                parent_label = FlowLabel.create_unique(item.org_dest.name,
                                                       item.org_dest)
            labels = flow_orig.labels.all()
            for label in labels:
                l_dest = FlowLabel.objects.filter(name=label.name,
                                                  org = item.org_dest).first()
                if not l_dest:
                    l_dest = FlowLabel.create_unique(label.name,
                                                     item.org_dest,
                                                     parent_label)
                l_dest.toggle_label([flow_dest], True)
    else:
        if item.archived and flow_dest:
            has_event = CampaignEvent.objects.filter(
                flow=flow_dest,
                campaign__org=item.org_dest,
                campaign__is_archived=False).exists()
            if not has_event:
                flow_dest.archive()


@task(track_started=True, name='migrate_flows_to_production')
def migrate_flows_to_production():
    from .models import Notification
    to_auto_migrate = Notification.objects.filter(
        is_automigrated = False,
        auto_migrated = True,
        item_type = Notification.FLOW_TYPE)
    admin_checked = Notification.objects.filter(
        migrated = False,
        is_active = False,
        item_type = Notification.FLOW_TYPE)
    all_notifications = admin_checked|to_auto_migrate
    all_notifications = all_notifications.order_by('-created_on')
    for item in all_notifications:
        migrate_one_flow(item)

def add_group_to_trigger(trigger_s, trigger_p):
    from temba.contacts.models import ContactGroup
    #Delete all old groups
    trigger_p.groups.all().delete()
    for g in trigger_s.groups.all():
        group = ContactGroup.get_user_group(trigger_p.org,
                                            g.name)
        if not group:
            group = ContactGroup.create_static(
                trigger_p.org,
                trigger_p.org.created_by,
                g.name)
        trigger_p.groups.add(group)

@task(track_started=True, name='migrate_trigger_to_production')
def migrate_trigger_to_production(notification):
    from .models import Notification
    from temba.triggers.models import Trigger

    item.mark_migrated()
    org_dest = notification.org_dest
    name = notification.item_name
    id_item = notification.item_id

    trigger_s = Trigger.objects.get(pk = id_item)
    trigger_p = Trigger.objects.filter(
        org = org_dest,
        keyword = trigger_s.keyword,
        trigger_type = trigger_s.trigger_type,
        flow__name = trigger_s.flow.name).last()

    if notification.accepted or notification.auto_migrated:
        if not trigger_p:
            flow = Flow.objects.filter(org = org_dest,
                                       name = trigger_s.flow.name
            ).first()
            if not flow:
                return
            trigger_p = Trigger.objects.create(
                created_by = org_dest.created_by,
                modified_by = org_dest.created_by,
                org = org_dest,
                keyword = trigger_s.keyword,
                trigger_type = trigger_s.trigger_type,
                flow = flow)
        add_group_to_trigger(trigger_s, trigger_p)
        if trigger_p.is_archived:
            trigger_p.restore(org_dest.created_by)
        else:
            if notification.archived and trigger_p:
                trigger_p.first().archive(org_dest.created_by)


@task(track_started=True, name='migrate_triggers_to_production')
def migrate_triggers_to_production():
    from .models import Notification
    from temba.triggers.models import Trigger

    to_auto_migrate = Notification.objects.filter(
        migrated = False,
        auto_migrated = True,
        item_type = Notification.TRIGGER_TYPE)
    admin_checked = Notification.objects.filter(
        migrated = False,
        is_active = False,
        item_type = Notification.TRIGGER_TYPE)

    all_notifications = admin_checked|to_auto_migrate
    all_notifications = all_notifications.order_by('-created_on')
    for item in all_notifications:
        migrate_trigger_to_production(item)

def auxiliar_create_campaign(campaign_orig, item):
    from temba.campaigns.models import Campaign, CampaignEvent, EventFire
    from temba.contacts.models import ContactGroup , ContactField
    campaign_orig = campaign_orig.first()
    group = ContactGroup.get_user_group(item.org_dest,
                                        campaign_orig.group.name)
    campaign_dest = Campaign.objects.filter(
        org = item.org_dest,
        name = campaign_orig.name)
    if not group:
        group = ContactGroup.create_static(
            item.org_dest,
            item.org_dest.created_by,
            campaign_orig.group.name)
    if not campaign_dest:
        campaign_name = Campaign.get_unique_name(
            item.org_dest,
            campaign_orig.name)
        campaign_dest = Campaign.create(item.org_dest,
                                        item.org_dest.created_by,
                                        campaign_name,
                                        group)
    else:
        campaign_dest = campaign_dest.first()
        campaign_dest.group = group
        campaign_dest.save()

    # we want to nuke old single message flows
    for event in campaign_dest.events.all():
        if event.flow.flow_type == Flow.MESSAGE:
            event.flow.release()
    # and all of the events, we'll recreate these
    campaign_dest.events.all().delete()
    # fill our campaign with events
    campaign_dest.is_archived = False
    campaign_dest.save()

    for event in campaign_orig.events.filter(is_active=True):
        relative_to = ContactField.get_or_create(item.org_dest,
                                                 item.org_dest.created_by,
                                                 key=event.relative_to.key,
                                                 label=event.relative_to.label)

        # create our message flow for message events
        if event.event_type == CampaignEvent.TYPE_MESSAGE:
            message = event.message
            base_language = 'base'
            if not isinstance(message, dict):
                try:
                    message = json.loads(message)
                except ValueError:
                    # if it's not a language dict, turn it into one
                    message = dict(base=message)
                    base_language = 'base'

            event = CampaignEvent.create_message_event(
                item.org_dest,
                item.org_dest.created_by,
                campaign_dest,
                relative_to,
                event.offset,
                event.unit,
                message,
                event.delivery_hour,
                base_language=base_language)
            event.update_flow_name()
        else:
            flow = Flow.objects.filter(
                org=item.org_dest,
                is_active=True,
                name = event.flow.name).first()
            if flow:
                CampaignEvent.create_flow_event(item.org_dest,
                                                item.org_dest.created_by,
                                                campaign_dest,
                                                relative_to,
                                                event.offset,
                                                event.unit,
                                                flow,
                                                event.delivery_hour)

    EventFire.update_campaign_events(campaign_dest)


@task(track_started=True, name='migrate_campaign_to_production')
def migrate_campaign_to_production():
    from .models import Notification
    from temba.campaigns.models import Campaign, EventFire
    to_auto_migrate = Notification.objects.filter(
        migrated = False,
        auto_migrated = True,
        item_type = Notification.CAMPAIGN_TYPE)
    admin_checked = Notification.objects.filter(
        migrated = False,
        is_active = False,
        item_type = Notification.CAMPAIGN_TYPE)
    all_notifications = admin_checked|to_auto_migrate
    all_notifications = all_notifications.order_by('-created_on')
    for item in all_notifications:
        if item.accepted:
            campaign_orig = Campaign.objects.filter(pk = item.item_id)
            if not campaign_orig:
                continue
            auxiliar_create_campaign(campaign_orig,item)
        else:
            campaign_dest = Campaign.objects.filter(
                org = item.org_dest,
                name = item.item_name)
            if campaign_dest and item.archived:
                c = campaign_dest.first()
                c.is_archived = True
                c.save()
                EventFire.update_campaign_events(c)
        item.mark_migrated()
