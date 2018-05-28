from django.db import migrations


def cleanup_dependencies(apps, schema_editor):
    CampaignEvent = apps.get_model("campaigns", "CampaignEvent")

    def release_flow(flow):
        if flow.flow_type == "M":
            flow.is_active = False
            flow.save()

            # remove our dependencies as well
            flow.field_dependencies.clear()
            flow.group_dependencies.clear()
            flow.flow_dependencies.clear()

    events = CampaignEvent.objects.filter(is_active=False, event_type="M", flow__is_active=True)
    for event in events:
        release_flow(event.flow)


class Migration(migrations.Migration):

    dependencies = [("campaigns", "0019_auto_20170608_1710")]

    operations = [migrations.RunPython(cleanup_dependencies)]
