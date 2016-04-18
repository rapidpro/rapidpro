# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings
import django.contrib.gis.db.models.fields


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunSQL('CREATE EXTENSION IF NOT EXISTS postgis'),
        migrations.RunSQL('CREATE EXTENSION IF NOT EXISTS postgis_topology'),
        migrations.CreateModel(
            name='AdminBoundary',
            fields=[
                ('id', models.AutoField(verbose_name='ID',
                                        serialize=False, auto_created=True, primary_key=True)),
                ('osm_id', models.CharField(
                    help_text=b'This is the OSM id for this administrative boundary', unique=True, max_length=15)),
                ('name', models.CharField(
                    help_text=b'The name of our administrative boundary', max_length=128)),
                ('level', models.IntegerField(
                    help_text=b'The level of the boundary, 0 for country, 1 for state, 2 for district')),
                ('geometry', django.contrib.gis.db.models.fields.MultiPolygonField(
                    help_text=b'The full geometry of this administrative boundary', srid=4326, null=True)),
                ('simplified_geometry', django.contrib.gis.db.models.fields.MultiPolygonField(
                    help_text=b'The simplified geometry of this administrative boundary', srid=4326, null=True)),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='BoundaryAlias',
            fields=[
                ('id', models.AutoField(verbose_name='ID',
                                        serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(
                    default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(
                    help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(
                    help_text=b'When this item was last modified', auto_now=True)),
                ('name', models.CharField(
                    help_text=b'The name for our alias', max_length=128)),
                ('boundary', models.ForeignKey(related_name=b'aliases', to='locations.AdminBoundary',
                                               help_text=b'The admin boundary this alias applies to')),
                ('created_by', models.ForeignKey(related_name=b'locations_boundaryalias_creations',
                                                 to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name=b'locations_boundaryalias_modifications',
                                                  to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
    ]
