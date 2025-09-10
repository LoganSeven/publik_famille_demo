from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0082_invoice_colour'),
    ]

    operations = [
        migrations.AddField(
            model_name='creditline',
            name='activity_label',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='creditline',
            name='agenda_slug',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='creditline',
            name='event_label',
            field=models.CharField(default='', max_length=260),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='creditline',
            name='event_slug',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
    ]
