from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('agendas', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='agenda',
            name='category_label',
            field=models.CharField(null=True, max_length=150, verbose_name='Category label'),
        ),
        migrations.AddField(
            model_name='agenda',
            name='category_slug',
            field=models.SlugField(null=True, max_length=160, verbose_name='Category identifier'),
        ),
    ]
