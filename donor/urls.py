from django.urls import path
from . import views

urlpatterns = [
    path('donorlogin/', views.donorlogin_view, name='donorlogin'),
    path('donorsignup/', views.donorsignup_view, name='donorsignup'),
    path('pending-approval/', views.donor_pending_approval_view, name='donor-pending-approval'),
    path('donor-dashboard/', views.donor_dashboard_view, name='donor-dashboard'),
    path('availability/', views.donor_set_availability_view, name='donor-set-availability'),
    path('donate-blood/', views.donate_blood_view, name='donate-blood'),
    path('my-donations/', views.donor_history_view, name='donor-history'),
    path('make-request/', views.donor_request_blood_view, name='donor-request-blood'),
    path('request-history/', views.donor_request_history_view, name='donor-request-history'),
    path('appointments/', views.donor_appointments_view, name='donor-appointments'),
    path('feedback/', views.donor_feedback_create_view, name='donor-feedback'),
    path('medical-reports/', views.donor_upload_medical_report_view, name='donor-medical-reports'),
]