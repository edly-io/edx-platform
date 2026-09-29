"""
Initial migration for Uber Learn Progress app.

Additive — creates three new tables. No existing tables are modified.
Safe to roll back with: ./manage.py lms migrate uber_learn zero
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UberLearnActivityProgress',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('course_key', models.CharField(db_index=True, max_length=255)),
                ('activity_key', models.CharField(max_length=255)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('last_submitted_at', models.DateTimeField(blank=True, null=True)),
                ('last_correct', models.BooleanField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('points_awarded', models.PositiveSmallIntegerField(default=0)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('modified', models.DateTimeField(auto_now=True)),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'app_label': 'uber_learn',
            },
        ),
        migrations.CreateModel(
            name='UberLearnAssessmentAttempt',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('course_key', models.CharField(db_index=True, max_length=255)),
                ('assessment_type', models.CharField(
                    choices=[
                        ('baseline', 'Baseline'),
                        ('final', 'Final'),
                        ('retention', 'Retention'),
                    ],
                    max_length=16,
                )),
                ('assessment_key', models.CharField(max_length=255)),
                ('attempt_number', models.PositiveIntegerField()),
                ('idempotency_key', models.UUIDField()),
                ('correct_count', models.PositiveSmallIntegerField()),
                ('total_count', models.PositiveSmallIntegerField()),
                ('passed', models.BooleanField(null=True)),
                ('question_results', models.JSONField(default=list)),
                ('submitted_at', models.DateTimeField(default=django.utils.timezone.now)),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'app_label': 'uber_learn',
            },
        ),
        migrations.CreateModel(
            name='UberLearnBadgeAward',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('course_key', models.CharField(db_index=True, max_length=255)),
                ('badge_type', models.CharField(
                    choices=[
                        ('applied', 'Applied'),
                        ('thorough', 'Thorough'),
                        ('retained', 'Retained'),
                    ],
                    max_length=16,
                )),
                ('awarded_at', models.DateTimeField(default=django.utils.timezone.now)),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'app_label': 'uber_learn',
            },
        ),
        # Constraints
        migrations.AddConstraint(
            model_name='uberlearnactivityprogress',
            constraint=models.UniqueConstraint(
                fields=['user', 'course_key', 'activity_key'],
                name='uber_learn_activity_user_course_activity_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='uberlearnassessmentattempt',
            constraint=models.UniqueConstraint(
                fields=['user', 'course_key', 'assessment_type', 'attempt_number'],
                name='uber_learn_assessment_attempt_number_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='uberlearnassessmentattempt',
            constraint=models.UniqueConstraint(
                fields=['user', 'idempotency_key'],
                name='uber_learn_assessment_idempotency_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='uberlearnbadgeaward',
            constraint=models.UniqueConstraint(
                fields=['user', 'course_key', 'badge_type'],
                name='uber_learn_badge_user_course_type_uniq',
            ),
        ),
        # Indexes
        migrations.AddIndex(
            model_name='uberlearnassessmentattempt',
            index=models.Index(
                fields=['user', 'course_key', 'assessment_type', 'passed'],
                name='uber_learn_assess_pass_idx',
            ),
        ),
    ]
