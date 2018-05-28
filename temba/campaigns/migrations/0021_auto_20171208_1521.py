from django.db import migrations

from temba.utils.languages import iso6392_to_iso6393


def migrate_event_languages(apps, schema_editor):
    from temba.campaigns.models import CampaignEvent

    events = CampaignEvent.objects.filter(event_type="M", is_active=True).select_related("campaign__org")
    total = len(events)
    for idx, event in enumerate(
        CampaignEvent.objects.filter(event_type="M", is_active=True).select_related("campaign__org")
    ):
        messages = {}
        for lang, message in event.message.items():
            if lang != "base":
                new_lang = iso6392_to_iso6393(lang, country_code=event.campaign.org.get_country_code())
                messages[new_lang] = message
            else:
                messages[lang] = message

        if idx % 1000 == 0:
            print("On event %d of %d" % (idx, total))

        event.message = messages
        event.save(update_fields=("message",))


class Migration(migrations.Migration):

    dependencies = [("campaigns", "0020_auto_20171030_1637")]

    operations = [migrations.RunPython(migrate_event_languages)]
