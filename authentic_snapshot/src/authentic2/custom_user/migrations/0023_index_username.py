from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('custom_user', '0022_index_email'),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                r'CREATE INDEX "custom_user_user_username_idx" ON "custom_user_user" (UPPER("username")'
                r' text_pattern_ops);'
            ),
            reverse_sql=r'DROP INDEX "custom_user_user_username_idx";',
        ),
    ]
