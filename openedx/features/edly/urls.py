"""
URLs Edly app views.
"""

from django.conf.urls import url

from openedx.features.edly import views

urlpatterns = [
    url('account_deactivated/', views.account_deactivated_view, name='account_deactivated_view'),
    url('otp/verify/', views.verify_otp_view, name='verify_otp'),
    url('otp/resend/', views.resend_otp_view, name='resend_otp'),
    url('otp/cancel/', views.cancel_two_factor_auth, name='cancel_two_factor_auth')
]
