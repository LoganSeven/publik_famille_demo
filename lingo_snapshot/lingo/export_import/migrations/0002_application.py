import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('export_import', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Application',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('name', models.CharField(max_length=100)),
                ('slug', models.SlugField(max_length=100, unique=True)),
                ('icon', models.FileField(blank=True, null=True, upload_to='applications/icons/')),
                ('description', models.TextField(blank=True)),
                ('documentation_url', models.URLField(blank=True)),
                ('version_number', models.CharField(max_length=100)),
                ('version_notes', models.TextField(blank=True)),
                ('editable', models.BooleanField(default=True)),
                ('visible', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='ApplicationElement',
            fields=[
                (
                    'id',
                    models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
                ),
                ('object_id', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'application',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to='export_import.application'
                    ),
                ),
                (
                    'content_type',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype'
                    ),
                ),
            ],
            options={
                'unique_together': {('application', 'content_type', 'object_id')},
            },
        ),
    ]
