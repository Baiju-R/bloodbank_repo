"""bloodbankmanagement URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
import os
from django.views.static import serve as static_serve
from blood import views as blood_views
from donor import views as donor_views
from patient import views as patient_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', blood_views.home_view, name='home'),

    # Authentication URLs
    path('adminlogin/', blood_views.adminlogin_view, name='adminlogin'),
    path('afterlogin/', blood_views.afterlogin_view, name='afterlogin'),
    path('logout/', blood_views.logout_view, name='logout'),

    # Admin URLs
    path('admin-dashboard/', blood_views.admin_dashboard_view, name='admin-dashboard'),
    path('admin-blood/', blood_views.admin_blood_view, name='admin-blood'),
    path('admin-donor/', blood_views.admin_donor_view, name='admin-donor'),
    path('admin-donor-map/', blood_views.admin_donor_map_view, name='admin-donor-map'),
    path('admin-patient/', blood_views.admin_patient_view, name='admin-patient'),
    path('admin-request/', blood_views.admin_request_view, name='admin-request'),
    path('admin-request/<int:pk>/broadcast/', blood_views.emergency_broadcast_view, name='emergency-broadcast'),
    path('admin-request/<int:pk>/recommendations/', blood_views.admin_request_recommendations_view, name='admin-request-recommendations'),
    path('admin-request-history/', blood_views.admin_request_history_view, name='admin-request-history'),
    path('admin-donation/', blood_views.admin_donation_view, name='admin-donation'),
    path('admin-analytics/', blood_views.admin_analytics_view, name='admin-analytics'),
    path('admin-leadership/', blood_views.admin_leadership_view, name='admin-leadership'),
    path('admin-appointments/', blood_views.admin_appointments_view, name='admin-appointments'),
    path('admin-appointments/<int:pk>/status/', blood_views.admin_appointment_update_status_view, name='admin-appointment-update-status'),
    path('admin-verification/', blood_views.admin_verification_view, name='admin-verification'),
    path('admin-audit-logs/', blood_views.admin_audit_logs_view, name='admin-audit-logs'),
    path('admin-reports/', blood_views.admin_reports_view, name='admin-reports'),
    path('admin-reports/export/<str:report_key>/<str:fmt>/', blood_views.admin_reports_export_view, name='admin-reports-export'),
    path('assistant/', blood_views.knowledge_chatbot_view, name='knowledge-chatbot'),

    # Feedback URLs
    path('feedback/', blood_views.public_feedback_create_view, name='public-feedback'),
    path('feedback/all/', blood_views.public_feedback_list_view, name='public-feedback-list'),
    path('terms/', blood_views.terms_and_conditions_view, name='terms-and-conditions'),
    path('admin-feedback/', blood_views.admin_feedback_list_view, name='admin-feedback-list'),
    path('admin-feedback/<int:pk>/', blood_views.admin_feedback_edit_view, name='admin-feedback-edit'),

    # Admin approval workflow
    path('admin-pending-approvals/', blood_views.admin_pending_approvals_view, name='admin-pending-approvals'),
    path('admin-approve-donor/<int:pk>/', blood_views.admin_approve_donor_view, name='admin-approve-donor'),
    path('admin-approve-patient/<int:pk>/', blood_views.admin_approve_patient_view, name='admin-approve-patient'),
    path('admin-verify-report/<int:pk>/', blood_views.admin_verify_report_view, name='admin-verify-report'),

    # Admin action URLs
    path('update-donor/<int:pk>/', blood_views.update_donor_view, name='update-donor'),
    path('delete-donor/<int:pk>/', blood_views.delete_donor_view, name='delete-donor'),
    path('update-patient/<int:pk>/', blood_views.update_patient_view, name='update-patient'),
    path('delete-patient/<int:pk>/', blood_views.delete_patient_view, name='delete-patient'),
    path('approve-request/<int:pk>/', blood_views.update_approve_status_view, name='approve-request'),
    path('reject-request/<int:pk>/', blood_views.update_reject_status_view, name='reject-request'),
    path('approve-donation/<int:pk>/', blood_views.approve_donation_view, name='approve-donation'),
    path('reject-donation/<int:pk>/', blood_views.reject_donation_view, name='reject-donation'),

    # Quick request URLs (for anonymous users)
    path('quick-request/', blood_views.quick_request_view, name='quick-request'),
    path('quick-request-success/<int:request_id>/', blood_views.quick_request_success_view, name='quick-request-success'),
    path('request-blood/', blood_views.request_blood_redirect_view, name='request-blood'),
    path('test-sms/', blood_views.test_sms, name='test-sms'),
    path('service-worker.js', blood_views.service_worker_js_view, name='service-worker-js'),

    # Forgot-password / password-reset (SMS OTP)
    path('forgot-password/<str:role>/', blood_views.forgot_password_view, name='forgot-password'),
    path('verify-otp/<str:role>/', blood_views.verify_otp_view, name='verify-otp'),
    path('reset-password/<str:role>/', blood_views.reset_password_view, name='reset-password'),

    # App URLs using include
    path('donor/', include('donor.urls')),
    path('patient/', include('patient.urls')),
]

# Serve media files during development, or explicitly in simple single-container deployments.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    serve_media = os.getenv('SERVE_MEDIA', 'false').lower() == 'true'
    if serve_media:
        urlpatterns += [
            re_path(r'^media/(?P<path>.*)$', static_serve, {'document_root': settings.MEDIA_ROOT}),
        ]
