# Generated by Django 2.2.4 on 2020-04-22 20:08

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("contacts", "0107_auto_20200422_2006")]

    operations = [migrations.RemoveField(model_name="contact", name="is_paused")]
