from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0059_apiclient_identifier_dedup'),
    ]

    operations = [
        migrations.AlterField(
            model_name='apiclient',
            name='identifier',
            field=models.CharField(max_length=256, unique=True, verbose_name='Identifier'),
        ),
    ]
