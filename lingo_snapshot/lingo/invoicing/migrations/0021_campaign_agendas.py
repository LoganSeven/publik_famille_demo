from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agendas', '0004_regie'),
        ('invoicing', '0020_campaign_label'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='agendas',
            field=models.ManyToManyField(related_name='campaigns', to='agendas.Agenda'),
        ),
    ]
