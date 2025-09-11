from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0009_auto_20160211_2247'),
    ]

    operations = [
        migrations.AddField(
            model_name='attributevalue',
            name='multiple',
            field=models.BooleanField(null=True),
            preserve_default=True,
        ),
    ]
