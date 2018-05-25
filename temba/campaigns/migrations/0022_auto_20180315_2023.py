from django.db import migrations


def set_datetime_types(apps, schema_editor):
    CampaignEvent = apps.get_model('campaigns', 'CampaignEvent')
    ContactField = apps.get_model('contacts', 'ContactField')

    # find all contact fields that events are based off which aren't a date
    non_date_fields = [ce.relative_to_id for ce in CampaignEvent.objects.all().exclude(relative_to__value_type='D')]

    # and change them to be dates
    ContactField.objects.filter(id__in=non_date_fields).update(value_type='D')


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0021_auto_20171208_1521'),
        ('contacts', '0074_create_fields_index'),
    ]

    operations = [
        migrations.RunPython(set_datetime_types)
    ]
