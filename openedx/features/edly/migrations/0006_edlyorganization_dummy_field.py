# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2020-11-24 10:19
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('edly', '0005_add_enable_all_edly_sub_org_login_boolean'),
    ]

    operations = [
        migrations.AddField(
            model_name='edlyorganization',
            name='dummy_field',
            field=models.CharField(help_text=b'Field added for migration rollback testing.', max_length=255, null=True),
        ),
    ]
