from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('invoicing', '0074_credit'),
    ]

    operations = [
        migrations.AddField(
            model_name='draftinvoiceline',
            name='event_slug',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='event_slug',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='event_label',
            field=models.CharField(default='', max_length=260),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='event_label',
            field=models.CharField(default='', max_length=260),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='creditline',
            name='slug',
            field=models.CharField(max_length=250),
        ),
        migrations.AlterField(
            model_name='draftinvoiceline',
            name='slug',
            field=models.CharField(max_length=250),
        ),
        migrations.AlterField(
            model_name='invoiceline',
            name='slug',
            field=models.CharField(max_length=250),
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='agenda_slug',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='agenda_slug',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='activity_label',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='activity_label',
            field=models.CharField(default='', max_length=250),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='draftinvoiceline',
            name='description',
            field=models.CharField(default='', max_length=500),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='description',
            field=models.CharField(default='', max_length=500),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='creditline',
            name='description',
            field=models.CharField(default='', max_length=500),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='draftjournalline',
            name='description',
            field=models.CharField(default='', max_length=500),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='journalline',
            name='description',
            field=models.CharField(default='', max_length=500),
            preserve_default=False,
        ),
    ]
