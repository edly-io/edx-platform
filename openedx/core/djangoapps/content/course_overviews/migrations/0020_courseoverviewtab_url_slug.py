# -*- coding: utf-8 -*-
# Generated by Django 1.11.28 on 2020-02-26 15:53


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('course_overviews', '0019_improve_courseoverviewtab'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseoverviewtab',
            name='url_slug',
            field=models.TextField(null=True),
        ),
    ]
