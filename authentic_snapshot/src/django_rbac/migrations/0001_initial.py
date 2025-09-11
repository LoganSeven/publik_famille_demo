from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Operation',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('name', models.CharField(max_length=32, verbose_name='name')),
                ('slug', models.CharField(unique=True, max_length=32, verbose_name='slug')),
            ],
            options={},
            bases=(models.Model,),
        ),
    ]
