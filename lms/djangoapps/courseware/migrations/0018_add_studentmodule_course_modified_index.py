from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('courseware', '0017_financialassistanceconfiguration'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='studentmodule',
            index=models.Index(fields=['course_id', 'modified'], name='courseware_course_modified'),
        ),
    ]
