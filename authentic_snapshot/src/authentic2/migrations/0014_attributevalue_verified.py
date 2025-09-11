from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0013_auto_20160211_2258'),
    ]

    operations = [
        migrations.AddField(
            model_name='attributevalue',
            name='verified',
            field=models.BooleanField(default=False),
        ),
    ]
