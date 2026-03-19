"""
Initial migration for Two Factor Authentication models.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailOTP',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('otp_hash', models.CharField(
                    help_text='SHA-256 hash of the 6-digit OTP code.',
                    max_length=64,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(
                    help_text='OTP is invalid after this time.',
                )),
                ('is_used', models.BooleanField(
                    default=False,
                    help_text='True once the OTP has been successfully verified.',
                )),
                ('user', models.ForeignKey(
                    db_index=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='email_otps',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Email OTP',
                'verbose_name_plural': 'Email OTPs',
                'ordering': ['-created_at'],
                'app_label': 'two_factor_auth',
            },
        ),
    ]
