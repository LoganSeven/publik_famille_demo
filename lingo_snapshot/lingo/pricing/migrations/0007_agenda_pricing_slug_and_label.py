from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0006_agenda_pricing_m2m'),
    ]

    operations = [
        migrations.AddField(
            model_name='agendapricing',
            name='label',
            field=models.CharField(max_length=150, null=True, verbose_name='Label'),
        ),
        migrations.AddField(
            model_name='agendapricing',
            name='slug',
            field=models.SlugField(max_length=160, null=True, verbose_name='Identifier'),
        ),
    ]
