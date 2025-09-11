from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('custom_user', '0021_set_unusable_password'),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                r'CREATE INDEX "custom_user_user_email_idx" ON "custom_user_user" (UPPER("email")'
                r' text_pattern_ops);'
            ),
            reverse_sql=r'DROP INDEX "custom_user_user_email_idx";',
        ),
    ]
