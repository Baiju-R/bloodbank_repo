from django.urls import path
from . import views

urlpatterns = [
    path('patientlogin/', views.patientlogin_view, name='patientlogin'),
    path('patientsignup/', views.patientsignup_view, name='patientsignup'),
    path('pending-approval/', views.patient_pending_approval_view, name='patient-pending-approval'),
    path('dashboard/', views.patient_dashboard_view, name='patient-dashboard'),
    path('make-request/', views.patient_request_view, name='make-request'),
    path('my-request/', views.patient_request_history_view, name='my-request'),
    path('nearby-donors/', views.patient_nearby_donors_view, name='patient-nearby-donors'),
    path('feedback/', views.patient_feedback_create_view, name='patient-feedback'),
]