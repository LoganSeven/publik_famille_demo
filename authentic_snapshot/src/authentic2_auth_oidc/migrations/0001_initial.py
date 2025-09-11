from django.conf import settings
from django.db import migrations, models
from django.db.models import JSONField

import authentic2.utils.jwc


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        migrations.swappable_dependency(settings.RBAC_OU_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OIDCAccount',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='creation date')),
                ('modified', models.DateTimeField(auto_now=True, verbose_name='last modification date')),
                ('sub', models.CharField(unique=True, max_length=256, verbose_name='sub')),
            ],
        ),
        migrations.CreateModel(
            name='OIDCClaimMapping',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('claim', models.CharField(max_length=64, verbose_name='claim')),
                ('attribute', models.CharField(max_length=64, verbose_name='attribute')),
                (
                    'verified',
                    models.PositiveIntegerField(
                        default=0,
                        verbose_name='verified',
                        choices=[(0, 'not verified'), (1, 'verified claim'), (2, 'always verified')],
                    ),
                ),
                ('required', models.BooleanField(blank=True, default=False, verbose_name='required')),
                (
                    'idtoken_claim',
                    models.BooleanField(blank=True, default=False, verbose_name='idtoken claim'),
                ),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='creation date')),
                ('modified', models.DateTimeField(auto_now=True, verbose_name='last modification date')),
            ],
            options={
                'verbose_name': 'Claim',
                'verbose_name_plural': 'Claims',
                'default_related_name': 'claim_mappings',
            },
        ),
        migrations.CreateModel(
            name='OIDCProvider',
            fields=[
                (
                    'id',
                    models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True),
                ),
                ('name', models.CharField(unique=True, max_length=128, verbose_name='name')),
                (
                    'issuer',
                    models.CharField(unique=True, max_length=256, verbose_name='issuer', db_index=True),
                ),
                ('client_id', models.CharField(max_length=128, verbose_name='client id')),
                (
                    'client_secret',
                    models.CharField(max_length=128, verbose_name='client secret'),
                ),
                (
                    'authorization_endpoint',
                    models.URLField(max_length=128, verbose_name='authorization endpoint'),
                ),
                ('token_endpoint', models.URLField(max_length=128, verbose_name='token endpoint')),
                ('userinfo_endpoint', models.URLField(max_length=128, verbose_name='userinfo endpoint')),
                (
                    'end_session_endpoint',
                    models.URLField(
                        max_length=128, null=True, verbose_name='end session endpoint', blank=True
                    ),
                ),
                ('scopes', models.CharField(max_length=128, verbose_name='scopes', blank=True)),
                (
                    'jwkset_json',
                    JSONField(
                        blank=True,
                        null=True,
                        verbose_name='JSON WebKey set',
                        validators=[authentic2.utils.jwc.validate_jwkset],
                    ),
                ),
                (
                    'idtoken_algo',
                    models.PositiveIntegerField(
                        default=1,
                        verbose_name='IDToken signature algorithm',
                        choices=[(0, 'none'), (1, 'RSA'), (2, 'HMAC'), (3, 'EC')],
                    ),
                ),
                (
                    'strategy',
                    models.CharField(
                        max_length=32,
                        verbose_name='strategy',
                        choices=[('create', 'create'), ('none', 'none')],
                    ),
                ),
                (
                    'max_auth_age',
                    models.PositiveIntegerField(null=True, verbose_name='max authentication age', blank=True),
                ),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='creation date')),
                ('modified', models.DateTimeField(auto_now=True, verbose_name='last modification date')),
                (
                    'ou',
                    models.ForeignKey(
                        verbose_name='organizational unit',
                        to=settings.RBAC_OU_MODEL,
                        on_delete=models.CASCADE,
                    ),
                ),
            ],
            options={
                'verbose_name': 'OpenID Connect',
            },
        ),
        migrations.AddField(
            model_name='oidcclaimmapping',
            name='provider',
            field=models.ForeignKey(
                related_name='claim_mappings',
                verbose_name='provider',
                to='authentic2_auth_oidc.OIDCProvider',
                on_delete=models.CASCADE,
            ),
        ),
        migrations.AddField(
            model_name='oidcaccount',
            name='provider',
            field=models.ForeignKey(
                related_name='accounts',
                verbose_name='provider',
                to='authentic2_auth_oidc.OIDCProvider',
                on_delete=models.CASCADE,
            ),
        ),
        migrations.AddField(
            model_name='oidcaccount',
            name='user',
            field=models.OneToOneField(
                related_name='oidc_account',
                verbose_name='user',
                to=settings.AUTH_USER_MODEL,
                on_delete=models.CASCADE,
            ),
        ),
    ]
