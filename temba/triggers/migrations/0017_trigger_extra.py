# Generated by Django 2.2.4 on 2020-04-20 15:06

from django.db import migrations
import temba.utils.models


class Migration(migrations.Migration):

    dependencies = [
        ('triggers', '0016_auto_20190816_1517'),
    ]

    operations = [
        migrations.AddField(
            model_name='trigger',
            name='extra',
            field=temba.utils.models.JSONAsTextField(default=dict, null=True),
        ),
    ]
