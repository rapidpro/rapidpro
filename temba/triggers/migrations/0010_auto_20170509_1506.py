from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('triggers', '0009_auto_20170508_1636'),
    ]

    operations = [
        migrations.AlterField(
            model_name='trigger',
            name='match_type',
            field=models.CharField(choices=[('F', 'Message starts with the keyword'), ('O', 'Message contains only the keyword')], default='F', help_text='How to match a message with a keyword', max_length=1, null=True, verbose_name='Trigger When'),
        ),
    ]
