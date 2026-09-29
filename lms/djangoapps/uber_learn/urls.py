"""
URLs for the Uber Learn Progress API.

Registered under: /api/uber_learn/v1/
"""
from django.urls import path

from lms.djangoapps.uber_learn import views

urlpatterns = [
    path('progress/<str:course_key>', views.ProgressView.as_view(), name='uber_learn_progress'),
    path('progress/<str:course_key>/activity', views.ActivityView.as_view(), name='uber_learn_activity'),
    path('progress/<str:course_key>/assessment', views.AssessmentView.as_view(), name='uber_learn_assessment'),
]
