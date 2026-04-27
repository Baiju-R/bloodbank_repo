from django.contrib import admin
from .models import (
    Stock,
    BloodRequest,
    ActionAuditLog,
    ReportExportLog,
    EmergencyBroadcast,
    BroadcastDelivery,
    InAppNotification,
    DonationAppointmentSlot,
    DonationAppointment,
    VerificationBadge,
)
from .models import Feedback

@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ['bloodgroup', 'unit']
    list_filter = ['bloodgroup']

@admin.register(BloodRequest)
class BloodRequestAdmin(admin.ModelAdmin):
    list_display = ['patient_name', 'bloodgroup', 'unit', 'status', 'date']
    list_filter = ['bloodgroup', 'status', 'date']
    search_fields = ['patient_name', 'reason']

@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
     list_display = ('author_type', 'author_label', 'rating', 'feedback_for', 'is_public', 'created_at')
     list_filter = ('author_type', 'feedback_for', 'rating', 'is_public')
     search_fields = ('display_name', 'message', 'admin_reply')


@admin.register(ActionAuditLog)
class ActionAuditLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'action',
        'entity_type',
        'entity_id',
        'bloodgroup',
        'units',
        'status_before',
        'status_after',
        'actor_username',
        'actor_role',
    )
    list_filter = ('action', 'entity_type', 'bloodgroup', 'status_after', 'created_at')
    search_fields = ('actor_username', 'notes', 'entity_id')


@admin.register(ReportExportLog)
class ReportExportLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'report_key', 'export_format', 'rows_exported', 'actor_username', 'status')
    list_filter = ('report_key', 'export_format', 'status', 'created_at')
    search_fields = ('actor_username', 'report_key', 'error')


@admin.register(EmergencyBroadcast)
class EmergencyBroadcastAdmin(admin.ModelAdmin):
    list_display = ('id', 'blood_request', 'status', 'total_targets', 'total_sent', 'total_failed', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('blood_request__patient_name', 'message')


@admin.register(BroadcastDelivery)
class BroadcastDeliveryAdmin(admin.ModelAdmin):
    list_display = ('id', 'broadcast', 'donor', 'channel', 'status', 'destination', 'delivered_at')
    list_filter = ('channel', 'status', 'created_at')
    search_fields = ('destination', 'detail', 'donor__user__username')


@admin.register(InAppNotification)
class InAppNotificationAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'donor', 'patient', 'is_read', 'created_at')
    list_filter = ('is_read', 'created_at')
    search_fields = ('title', 'message')


@admin.register(DonationAppointmentSlot)
class DonationAppointmentSlotAdmin(admin.ModelAdmin):
    list_display = ('id', 'start_at', 'end_at', 'capacity', 'is_active', 'created_by')
    list_filter = ('is_active', 'start_at')


@admin.register(DonationAppointment)
class DonationAppointmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'donor', 'slot', 'status', 'requested_for', 'requested_at')
    list_filter = ('status', 'requested_at')
    search_fields = ('donor__user__username', 'notes')


@admin.register(VerificationBadge)
class VerificationBadgeAdmin(admin.ModelAdmin):
    list_display = ('id', 'badge_name', 'donor', 'patient', 'is_verified', 'trust_score', 'verified_at')
    list_filter = ('is_verified', 'badge_name', 'verified_at')
    search_fields = ('badge_name', 'hospital_name', 'notes')
