# -*- coding: utf-8 -*-


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('grades', '0008_persistentsubsectiongrade_first_attempted'),
    ]

    operations = [
        migrations.AlterIndexTogether(
            name='persistentcoursegrade',
            index_together=set([('passed_timestamp', 'course_id'), ('modified', 'course_id')]),
        ),
        migrations.AlterIndexTogether(
            name='persistentsubsectiongrade',
            index_together=set([('modified', 'course_id', 'usage_key')]),
        ),
    ]
