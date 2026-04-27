from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from .models import Donor, BloodDonate, MedicalReport
from blood.utils.phone import normalize_phone_number

class DonorUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'password']
        widgets = {
            'password': forms.PasswordInput()
        }

    def clean_password(self):
        password = self.cleaned_data.get('password')
        validate_password(password)
        return password

class DonorForm(forms.ModelForm):
    BLOOD_GROUP_CHOICES = [
        ('A+', 'A+'),
        ('A-', 'A-'),
        ('B+', 'B+'),
        ('B-', 'B-'),
        ('AB+', 'AB+'),
        ('AB-', 'AB-'),
        ('O+', 'O+'),
        ('O-', 'O-'),
    ]
    
    bloodgroup = forms.ChoiceField(choices=BLOOD_GROUP_CHOICES, widget=forms.Select(attrs={'class': 'form-control'}))
    latitude = forms.DecimalField(
        required=False,
        min_value=-90,
        max_value=90,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
        help_text='Optional. Example: 12.971598'
    )
    longitude = forms.DecimalField(
        required=False,
        min_value=-180,
        max_value=180,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
        help_text='Optional. Example: 77.594566'
    )
    
    class Meta:
        model = Donor
        fields = [
            'aadhaar_number',
            'bloodgroup', 'address', 'mobile', 'latitude', 'longitude', 'zipcode', 'profile_pic',
            # Medical / eligibility (optional but recommended for smart matching)
            'sex', 'date_of_birth', 'weight_kg', 'hemoglobin_g_dl',
            'blood_pressure_systolic', 'blood_pressure_diastolic',
            'has_chronic_disease', 'chronic_disease_details',
            'on_medication', 'medication_details',
            'smokes',
        ]
        widgets = {
            'aadhaar_number': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '12', 'pattern': '\\d{12}', 'placeholder': '123456789012'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'mobile': forms.TextInput(attrs={'class': 'form-control'}),
            'zipcode': forms.TextInput(attrs={'class': 'form-control'}),
            'profile_pic': forms.FileInput(attrs={'class': 'form-control-file'})
        }

    def clean(self):
        cleaned_data = super().clean()
        lat = cleaned_data.get('latitude')
        lng = cleaned_data.get('longitude')

        if (lat is None) != (lng is None):
            raise forms.ValidationError(
                'Please provide both latitude and longitude or leave both blank.'
            )

        return cleaned_data

    def clean_mobile(self):
        mobile = self.cleaned_data.get('mobile')
        normalized = normalize_phone_number(mobile)
        if not normalized:
            raise forms.ValidationError('Enter a valid phone number (preferably with country code).')
        return normalized

class BloodDonateForm(forms.ModelForm):
    class Meta:
        model = BloodDonate
        fields = ['bloodgroup', 'unit', 'disease', 'age']

    def clean_unit(self):
        unit = self.cleaned_data.get('unit')
        if unit is None or unit < 1:
            raise forms.ValidationError('At least 1 unit must be donated.')
        if unit > 10:
            raise forms.ValidationError('Cannot donate more than 10 units at once.')
        return unit

    def clean_age(self):
        age = self.cleaned_data.get('age')
        if age is not None and (age < 18 or age > 65):
            raise forms.ValidationError('Donors must be between 18 and 65 years old.')
        return age


class DonorUserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'})
        }


class DonorAdminUpdateForm(forms.ModelForm):
    """Admin-facing update form with broader field coverage.

    This form is used by the custom admin dashboard (not Django admin site) so admins can
    edit all donor details in one place.
    """

    BLOOD_GROUP_CHOICES = DonorForm.BLOOD_GROUP_CHOICES

    bloodgroup = forms.ChoiceField(choices=BLOOD_GROUP_CHOICES, widget=forms.Select(attrs={'class': 'form-control'}))
    latitude = forms.DecimalField(
        required=False,
        min_value=-90,
        max_value=90,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
        help_text='Optional. Example: 12.971598'
    )
    longitude = forms.DecimalField(
        required=False,
        min_value=-180,
        max_value=180,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
        help_text='Optional. Example: 77.594566'
    )

    class Meta:
        model = Donor
        exclude = ['user']
        widgets = {
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'mobile': forms.TextInput(attrs={'class': 'form-control'}),
            'zipcode': forms.TextInput(attrs={'class': 'form-control'}),
            'profile_pic': forms.FileInput(attrs={'class': 'form-control-file'}),
            'date_of_birth': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'weight_kg': forms.NumberInput(attrs={'class': 'form-control'}),
            'hemoglobin_g_dl': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
            'blood_pressure_systolic': forms.NumberInput(attrs={'class': 'form-control'}),
            'blood_pressure_diastolic': forms.NumberInput(attrs={'class': 'form-control'}),
            'chronic_disease_details': forms.TextInput(attrs={'class': 'form-control'}),
            'medication_details': forms.TextInput(attrs={'class': 'form-control'}),
            'last_donated_at': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.coords_cleared = False
        # Make boolean and select widgets consistent
        for name, field in self.fields.items():
            if getattr(field.widget, 'input_type', '') == 'checkbox':
                field.widget.attrs.setdefault('class', 'form-check-input')
            if isinstance(field.widget, forms.Select) and 'class' not in field.widget.attrs:
                field.widget.attrs['class'] = 'form-control'

    def clean(self):
        cleaned_data = super().clean()
        lat = cleaned_data.get('latitude')
        lng = cleaned_data.get('longitude')

        # Admin-friendly: if only one coordinate is provided, clear both.
        # This avoids blocking profile updates when legacy data has partial coords.
        if (lat is None) != (lng is None):
            cleaned_data['latitude'] = None
            cleaned_data['longitude'] = None
            self.coords_cleared = True

        return cleaned_data

    def clean_mobile(self):
        mobile = self.cleaned_data.get('mobile')
        normalized = normalize_phone_number(mobile)
        if not normalized:
            raise forms.ValidationError('Enter a valid phone number (preferably with country code).')
        return normalized


class MedicalReportForm(forms.ModelForm):
    """Form for donors to upload medical health reports."""
    class Meta:
        model = MedicalReport
        fields = ['document', 'notes']
        widgets = {
            'document': forms.FileInput(attrs={
                'class': 'form-control-file',
                'accept': '.pdf,.jpg,.jpeg,.png,.doc,.docx',
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'Optional notes about this report',
            }),
        }

    def clean_document(self):
        doc = self.cleaned_data.get('document')
        if doc:
            # Validate file size (max 10MB)
            if doc.size > 10 * 1024 * 1024:
                raise forms.ValidationError('File size must be under 10 MB.')
            # Validate file extension
            allowed_exts = ['.pdf', '.jpg', '.jpeg', '.png', '.doc', '.docx', '.bmp', '.tiff']
            import os
            ext = os.path.splitext(doc.name)[1].lower()
            if ext not in allowed_exts:
                raise forms.ValidationError(
                    f'Unsupported file type ({ext}). Allowed: {", ".join(allowed_exts)}'
                )
        return doc