# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2018-07-16 16:24
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    initial = True

    dependencies = [("channels", "0096_auto_20180716_1623")]

    operations = [
        migrations.CreateModel(
            name="IVRCall", fields=[], options={"proxy": True, "indexes": []}, bases=("channels.channelsession",)
        )
    ]
