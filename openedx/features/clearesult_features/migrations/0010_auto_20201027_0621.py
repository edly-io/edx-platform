# Generated by Django 2.2.15 on 2020-10-27 06:21

from django.db import migrations
import openedx.features.clearesult_features.models


class Migration(migrations.Migration):

    dependencies = [
        ('clearesult_features', '0009_auto_20201026_1414'),
    ]

    operations = [
        migrations.AlterField(
            model_name='clearesultsiteconfiguration',
            name='security_code',
            field=openedx.features.clearesult_features.models.EncryptedTextField(max_length=20, verbose_name='Site security code'),
        ),
        migrations.AlterField(
            model_name='clearesultusersiteprofile',
            name='saved_security_code',
            field=openedx.features.clearesult_features.models.EncryptedTextField(max_length=20, verbose_name='Saved site security code'),
        ),
    ]
