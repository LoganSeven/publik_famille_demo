import uuid

import django.contrib.postgres.fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('invoicing', '0124_date_payment_deadline_displayed'),
    ]

    operations = [
        migrations.CreateModel(
            name='CampaignAsyncJob',
            fields=[
                (
                    'uuid',
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('registered', 'Registered'),
                            ('waiting', 'Waiting'),
                            ('running', 'Running'),
                            ('failed', 'Failed'),
                            ('completed', 'Completed'),
                        ],
                        default='registered',
                        max_length=100,
                    ),
                ),
                ('exception', models.TextField()),
                ('action', models.CharField(max_length=100)),
                ('params', models.JSONField(default=dict)),
                ('total_count', models.PositiveIntegerField(default=0)),
                ('current_count', models.PositiveIntegerField(default=0)),
                ('failure_label', models.TextField(blank=True)),
                ('result_data', models.JSONField(default=dict)),
                ('creation_timestamp', models.DateTimeField(auto_now_add=True)),
                ('last_update_timestamp', models.DateTimeField(auto_now=True)),
                ('completion_timestamp', models.DateTimeField(default=None, null=True)),
                (
                    'campaign',
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='invoicing.campaign'),
                ),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='PoolAsyncJob',
            fields=[
                (
                    'uuid',
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True
                    ),
                ),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('registered', 'Registered'),
                            ('waiting', 'Waiting'),
                            ('running', 'Running'),
                            ('failed', 'Failed'),
                            ('completed', 'Completed'),
                        ],
                        default='registered',
                        max_length=100,
                    ),
                ),
                ('exception', models.TextField()),
                ('action', models.CharField(max_length=100)),
                ('params', models.JSONField(default=dict)),
                ('total_count', models.PositiveIntegerField(default=0)),
                ('current_count', models.PositiveIntegerField(default=0)),
                ('failure_label', models.TextField(blank=True)),
                ('result_data', models.JSONField(default=dict)),
                ('creation_timestamp', models.DateTimeField(auto_now_add=True)),
                ('last_update_timestamp', models.DateTimeField(auto_now=True)),
                ('completion_timestamp', models.DateTimeField(default=None, null=True)),
                ('users', models.JSONField(default=dict)),
                (
                    'campaign_job',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to='invoicing.campaignasyncjob',
                    ),
                ),
                ('pool', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='invoicing.pool')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
