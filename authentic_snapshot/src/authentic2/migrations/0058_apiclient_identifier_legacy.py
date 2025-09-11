from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authentic2', '0057_remove_attributevalue_verification_sources'),
    ]

    operations = [
        migrations.AddField(
            model_name='apiclient',
            name='identifier_legacy',
            field=models.CharField(max_length=256, null=True, default=None, verbose_name='Legacy identifier'),
        ),
    ]
