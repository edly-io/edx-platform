# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-01-28 07:28
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('course_modes', '0011_change_regex_for_comma_separated_ints'),
    ]

    operations = [
        migrations.AlterField(
            model_name='coursemode',
            name='currency',
            field=models.CharField(default='PKR', max_length=8),
        ),
        migrations.AlterField(
            model_name='coursemodesarchive',
            name='currency',
            field=models.CharField(default='PKR', max_length=8),
        ),
    ]
