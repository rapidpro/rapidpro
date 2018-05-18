from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('locations', '0007_reset_2'),
    ]

    operations = [
        migrations.RunSQL(
            'CREATE INDEX CONCURRENTLY locations_adminboundary_name on locations_adminboundary(upper("name"))'),
        migrations.RunSQL(
            'CREATE INDEX CONCURRENTLY locations_boundaryalias_name on locations_boundaryalias(upper("name"))')
    ]
