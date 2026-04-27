from django.urls import path
from . import views

urlpatterns = [
    path('', views.home_view, name='home'),
    path('terms/', views.terms_and_conditions_view, name='terms-and-conditions'),
    path('adminlogin/', views.adminlogin_view, name='adminlogin'),
    path('logout/', views.logout_view, name='logout'),
    path('afterlogin/', views.afterlogin_view, name='afterlogin'),
    
    # Admin URLs
    path('admin-dashboard/', views.admin_dashboard_view, name='admin-dashboard'),
    path('admin-blood/', views.admin_blood_view, name='admin-blood'),
    path('admin-donor/', views.admin_donor_view, name='admin-donor'),
    path('admin-donor-map/', views.admin_donor_map_view, name='admin-donor-map'),
    path('admin-patient/', views.admin_patient_view, name='admin-patient'),
    path('admin-request/', views.admin_request_view, name='admin-request'),
    path('admin-request/<int:pk>/broadcast/', views.emergency_broadcast_view, name='emergency-broadcast'),
    path('admin-request/<int:pk>/recommendations/', views.admin_request_recommendations_view, name='admin-request-recommendations'),
    path('admin-request-history/', views.admin_request_history_view, name='admin-request-history'),
    path('admin-donation/', views.admin_donation_view, name='admin-donation'),
    path('admin-analytics/', views.admin_analytics_view, name='admin-analytics'),
    path('admin-leadership/', views.admin_leadership_view, name='admin-leadership'),
    path('admin-appointments/', views.admin_appointments_view, name='admin-appointments'),
    path('admin-appointments/<int:pk>/status/', views.admin_appointment_update_status_view, name='admin-appointment-update-status'),
    path('admin-verification/', views.admin_verification_view, name='admin-verification'),
    path('admin-audit-logs/', views.admin_audit_logs_view, name='admin-audit-logs'),
    path('admin-reports/', views.admin_reports_view, name='admin-reports'),
    path('admin-reports/export/<str:report_key>/<str:fmt>/', views.admin_reports_export_view, name='admin-reports-export'),
    
    # Admin Actions
    path('update-donor/<int:pk>/', views.update_donor_view, name='update-donor'),
    path('delete-donor/<int:pk>/', views.delete_donor_view, name='delete-donor'),
    path('update-patient/<int:pk>/', views.update_patient_view, name='update-patient'),
    path('delete-patient/<int:pk>/', views.delete_patient_view, name='delete-patient'),
    path('approve-request/<int:pk>/', views.update_approve_status_view, name='approve-request'),
    path('reject-request/<int:pk>/', views.update_reject_status_view, name='reject-request'),
    path('approve-request/<int:pk>/retry-sms/', views.retry_approval_sms_view, name='retry-approval-sms'),
    path('approve-donation/<int:pk>/', views.approve_donation_view, name='approve-donation'),
    path('reject-donation/<int:pk>/', views.reject_donation_view, name='reject-donation'),
    
    # Utility URLs
    path('request-blood/', views.request_blood_redirect_view, name='request-blood-redirect'),
    path('test-sms/', views.test_sms, name='test-sms'),
    path('service-worker.js', views.service_worker_js_view, name='service-worker-js'),
]
