import json

from django.utils import timezone
from celery.task import task
from temba.flows.models import  FlowLabel, Flow, FlowRevision
from temba.triggers.models import Trigger
from temba.campaigns.models import Campaign, EventFire, CampaignEvent
from temba.contacts.models import ContactField
from .models import Notification

BASE_IMPORT = {
  "version": "11.4",
  "site": "https://rapidpro.datos.gob.mx",
  "flows": [],
  "campaigns": [],
  "triggers": []
  }
def create_labels(notification, label, parent_label, flow_dest):
    if label.parent:
        p_label =create_labels(notification, label.parent, parent_label, flow_dest)
    else:
        p_label = parent_label
    l_dest = FlowLabel.objects.filter(name=label.name,
                                      parent__name = p_label.name,
                                      org = notification.org_dest).last()
    if not l_dest:
        l_dest = FlowLabel.create_unique(label.name,
                                         notification.org_dest,
                                         p_label)
    l_dest.toggle_label([flow_dest], True)
    return l_dest if label.parent else parent_label


def migrate_flow_to_production(notification):
    flow_orig = Flow.objects.filter(id = notification.item_id).last()
    definition = BASE_IMPORT.copy()
    definition["flows"] = [json.loads(notification.history)]
    notification.org_dest.import_app(definition,
                        notification.created_by)
    flow_dest = Flow.objects.filter(name = notification.item_name,
                                    org = notification.org_dest).last()
    flow_dest.restore()
    if flow_orig and flow_dest:
        #Now migrate the labels, get or create origin_org label
        parent_label = FlowLabel.objects.filter(name = notification.org_orig.name,
                                                org = notification.org_dest).last()
        if not parent_label:
            parent_label = FlowLabel.create_unique(notification.org_orig.name,
                                                   notification.org_dest)


        labels = flow_orig.labels.all()
        for label in labels:
            create_labels(notification, label, parent_label, flow_dest)


def migrate_flows_to_production(all_notifications):
    for n in all_notifications:
        migrate_flow_to_production(n)
        n.mark_migrated()


def migrate_triggers_to_production(all_notifications):
    for n in all_notifications:
        trigger = json.loads(history)
        definition = BASE_IMPORT.copy()
        definition["triggers"] = [json.loads(history)]
        n.org_dest.import_app(definition,
                            notification.created_by)
        n.mark_migrated()
        t = Trigger.objects.filter(org = n.org_dest,
                                   keyword = trigger["keyword"],
                                   flow__name = trigger["flow"]["name"],
                                   trigger_type = trigger["trigger_type"]
                                   ).last()
        t.restore(n.created_by)



def migrate_campaigns_to_production(all_notifications):
    for n in all_notifications:
        campaign = json.loads(n.history)
        definition = BASE_IMPORT.copy()
        definition["campaign"] = [campaign]
        n.org_dest.import_app(definition,
                        n.created_by)
        n.mark_migrated()
        c = Campaign.objects.filter(name = n.item_name,
                                    org = n.org_dest,
                                    group__name =campaign["group"]["name"]).last()
        Campaign.restore_flows(c)
        EventFire.update_campaign_events(c)


def create_event(event_spec, notification, campaign):
    org = notification.org_dest
    user = notification.created_by

    relative_to = ContactField.get_or_create(
        org,
        user,
        key=event_spec["relative_to"]["key"],
        label=event_spec["relative_to"]["label"],
        value_type="D",
    )
    # create our message flow for message events
    if event_spec["event_type"] == CampaignEvent.TYPE_MESSAGE:
        message = event_spec["message"]
        base_language = event_spec.get("base_language")
        if not isinstance(message, dict):
            try:
                message = json.loads(message)
            except ValueError:
                # if it's not a language dict, turn it into one
                message = dict(base=message)
                base_language = "base"
        event = CampaignEvent.create_message_event(
            org,
            user,
            campaign,
            relative_to,
            event_spec["offset"],
            event_spec["unit"],
            message,
            event_spec["delivery_hour"],
            base_language=base_language,
        )
        event.update_flow_name()
    else:
        flow = Flow.objects.filter(org=org, is_active=True, name=event_spec["flow"]["name"]).last()
        if flow:
            CampaignEvent.create_flow_event(
                org,
                user,
                campaign,
                relative_to,
                event_spec["offset"],
                event_spec["unit"],
                flow,
                event_spec["delivery_hour"],
            )


def migrate_events_to_production(all_notifications):
    for n in all_notifications:
        #Check if campaign exist
        item = json.loads(n.history)
        c = Campaign.objects.filter(org = n.org_dest,
                                    name = item["name"],
                                    group__name = item["group"]["name"]
                                    ).last()


        if c :
            c.is_archived=False
            c.modified_by=n.created_by
            c.modified_on=timezone.now()
            c.save()
            for event_spec in item["events"]:
                #check if our event is in campaign
                e  = c.events.filter(offset = event_spec ["offset"],
                                    unit = event_spec["unit"],
                                    relative_to__key =event_spec["relative_to"]["key"],
                                    flow__name = event_spec["flow"]["name"],
                                    event_type =event_spec["event_type"])
                if not e:
                    create_event(event_spec, n, c)
                    print("Se creo el evento")
                else:
                    print ("Evento ya existente")
        else:
            definition = BASE_IMPORT.copy()
            definition["campaign"] = item
            definition["flows"] =json.dumps(FlowRevision.objects.filter(flow__name=item.item_name).last().definition)
            n.org_dest.import_app(definition,
                            n.created_by)

            c = Campaign.objects.filter(name = item["name"],
                                        org = n.org_dest,
                                        group__name =item["group"]["name"]).last()
            print(c)
            print (item)
            print (n.org_dest)
            Campaign.restore_flows(c)
            print("Se creo toda la campania")
        EventFire.update_campaign_events(c)
        n.mark_migrated()


@task(track_started=True, name='notification_migrate_changes')
def migrate_changes():
    to_auto_migrate = Notification.objects.filter(
        migrated = False,
        auto_migrated = True)
    admin_checked = Notification.objects.filter(
        migrated = False,
        reviewed = True,
        accepted = True)
    notifications = admin_checked|to_auto_migrate
    notifications = notifications.order_by('created_on')

    migrate_flows_to_production(notifications.filter(item_type = Notification.FLOW_TYPE))
    migrate_triggers_to_production(notifications.filter(item_type = Notification.TRIGGER_TYPE))
    migrate_campaigns_to_production(notifications.filter(item_type = Notification.CAMPAIGN_TYPE))
    migrate_events_to_production(notifications.filter(item_type = Notification.EVENT_TYPE))


@task(track_started=True, name='notification_archive_changes')
def archive_changes():
    admin_checked = Notification.objects.filter(
        reviewed = True,
        to_archive = True,
        archived = False)

    #campaigns
    campaigns = admin_checked.filter(item_type =  Notification.CAMPAIGN_TYPE)
    for n in campaigns:
        c = Campaign.objects.filter(org= n.org_dest, name = n.item_name).last()
        if c:
            c.is_archived = True
            c.save()
            EventFire.update_campaign_events(c)
        n.mark_archived()
    #triggers
    triggers = admin_checked.filter(item_type =  Notification.TRIGGER_TYPE)
    for n in triggers:
        t = Trigger.objects.filter(org= n.org_orig, name = n.item_id).last()
        t_prod =  Trigger.objects.filter(org = n.org_dest,
                                        keyword = t.keyword,
                                        trigger_type = t.trigger_type,
                                        flow__name = t.flow.name,
                                        is_archived = False).last()
        if t_prod:
            trigger.archive(n.created_by)
        n.mark_archived()
    #flows
    flows = admin_checked.filter(item_type =  Notification.FLOW_TYPE)
    for n in flows:
        f = Flow.objects.filter(org = n.org_dest, name = n.item_name).last()
        if f:
            print (f)
            f.archive()
            c_event = CampaignEvent.objects.filter(flow = f).last()
            trigger = Trigger.objects.filter(flow= f).last()
            if c_event:
                print (c_event)
                c_event.release()
            if trigger:
                print (trigger)
                trigger.archive(n.created_by)
        n.mark_archived()
