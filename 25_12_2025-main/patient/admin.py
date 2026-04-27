from django import forms
from django.contrib import admin

from .models import Patient


class PatientAdminForm(forms.ModelForm):
    first_name = forms.CharField(required=False, label="First name")
    last_name = forms.CharField(required=False, label="Last name")
    username = forms.CharField(required=False)
    email = forms.EmailField(required=False)

    class Meta:
        model = Patient
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and getattr(self.instance, "user_id", None):
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name
            self.fields["username"].initial = self.instance.user.username
            self.fields["email"].initial = self.instance.user.email

    def save(self, commit=True):
        patient = super().save(commit=False)
        user = getattr(patient, "user", None)
        if user is not None:
            user.first_name = self.cleaned_data.get("first_name", "") or ""
            user.last_name = self.cleaned_data.get("last_name", "") or ""
            new_username = self.cleaned_data.get("username")
            if new_username is not None:
                user.username = new_username
            user.email = self.cleaned_data.get("email", "") or ""
            if commit:
                user.save()

        if commit:
            patient.save()
            self.save_m2m()
        return patient

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    form = PatientAdminForm
    list_display = ['get_name', 'bloodgroup', 'mobile', 'disease']
    list_filter = ['bloodgroup', 'disease']
    search_fields = ['user__first_name', 'user__last_name', 'mobile']

    fieldsets = (
        ("User", {"fields": ("first_name", "last_name", "username", "email", "user")}),
        ("Patient", {"fields": ("age", "bloodgroup", "disease", "doctorname", "address", "mobile", "profile_pic")}),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly.append("user")
        return tuple(readonly)
