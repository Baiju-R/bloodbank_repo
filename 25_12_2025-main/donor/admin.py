from django import forms
from django.contrib import admin
from django.contrib.auth.models import User

from .models import Donor, BloodDonate


class DonorAdminForm(forms.ModelForm):
    first_name = forms.CharField(required=False, label="First name")
    last_name = forms.CharField(required=False, label="Last name")
    username = forms.CharField(required=False)
    email = forms.EmailField(required=False)

    class Meta:
        model = Donor
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and getattr(self.instance, "user_id", None):
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name
            self.fields["username"].initial = self.instance.user.username
            self.fields["email"].initial = self.instance.user.email

    def save(self, commit=True):
        donor = super().save(commit=False)
        user = getattr(donor, "user", None)
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
            donor.save()
            self.save_m2m()
        return donor

@admin.register(Donor)
class DonorAdmin(admin.ModelAdmin):
    form = DonorAdminForm
    list_display = ['get_name', 'bloodgroup', 'mobile', 'is_available', 'availability_updated_at']
    list_filter = ['bloodgroup', 'is_available']
    search_fields = ['user__first_name', 'user__last_name', 'mobile']

    fieldsets = (
        ("User", {"fields": ("first_name", "last_name", "username", "email", "user")}),
        (
            "Donor",
            {
                "fields": (
                    "bloodgroup",
                    "mobile",
                    "address",
                    "zipcode",
                    "profile_pic",
                    "latitude",
                    "longitude",
                    "location_verified",
                    "is_available",
                    "availability_updated_at",
                    "last_notified_at",
                    "last_donated_at",
                    "sex",
                    "date_of_birth",
                    "weight_kg",
                    "hemoglobin_g_dl",
                    "blood_pressure_systolic",
                    "blood_pressure_diastolic",
                    "has_chronic_disease",
                    "chronic_disease_details",
                    "on_medication",
                    "medication_details",
                    "smokes",
                )
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly.append("user")
        return tuple(readonly)

@admin.register(BloodDonate)
class BloodDonateAdmin(admin.ModelAdmin):
    list_display = ['donor', 'bloodgroup', 'unit', 'status', 'date']
    list_filter = ['bloodgroup', 'status', 'date']
    search_fields = ['donor__user__first_name', 'donor__user__last_name']
