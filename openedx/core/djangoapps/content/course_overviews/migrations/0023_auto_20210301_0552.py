# Generated by Django 2.2.17 on 2021-03-01 05:52

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('course_overviews', '0022_courseoverviewtab_is_hidden'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseoverview',
            name='course_for',
            field=models.TextField(default='public'),
        ),
        migrations.AddField(
            model_name='historicalcourseoverview',
            name='course_for',
            field=models.TextField(default='public'),
        ),
    ]
