import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('callback', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Callback',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('object_id', models.PositiveIntegerField()),
                ('notification_type', models.CharField(max_length=50)),
                (
                    'payload',
                    models.JSONField(
                        blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder
                    ),
                ),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('registered', 'Registered'),
                            ('running', 'Running'),
                            ('toretry', 'To retry'),
                            ('failed', 'Failed'),
                            ('completed', 'Completed'),
                        ],
                        default='registered',
                        max_length=15,
                    ),
                ),
                ('retries_counter', models.IntegerField(default=0)),
                ('retry_reason', models.CharField(max_length=250, blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True, null=True)),
                (
                    'content_type',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype'
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name='callback',
            index=models.Index(fields=['content_type', 'object_id'], name='callback_ca_content_485ae4_idx'),
        ),
    ]
