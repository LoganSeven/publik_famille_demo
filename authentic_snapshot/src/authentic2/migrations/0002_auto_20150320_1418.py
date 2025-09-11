from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='attribute',
            name='kind',
            field=models.CharField(max_length=16, verbose_name='kind'),
            preserve_default=True,
        ),
    ]
