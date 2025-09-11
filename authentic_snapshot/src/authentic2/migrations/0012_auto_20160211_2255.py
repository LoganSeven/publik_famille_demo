from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0011_auto_20160211_2253'),
    ]

    operations = [
        migrations.AlterField(
            model_name='attributevalue',
            name='multiple',
            field=models.BooleanField(default=False, null=True),
        ),
    ]
