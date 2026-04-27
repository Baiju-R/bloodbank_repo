from django import forms
from . import models

class BloodForm(forms.ModelForm):
    class Meta:
        model = models.Stock
        fields = ['bloodgroup', 'unit']
        widgets = {
            'bloodgroup': forms.Select(choices=[
                ('A+', 'A+'), ('A-', 'A-'), ('B+', 'B+'), ('B-', 'B-'),
                ('AB+', 'AB+'), ('AB-', 'AB-'), ('O+', 'O+'), ('O-', 'O-')
            ], attrs={'class': 'form-control'}),
            'unit': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'})
        }

class RequestForm(forms.ModelForm):
    class Meta:
        model = models.BloodRequest
        fields = ['patient_name', 'patient_age', 'reason', 'bloodgroup', 'unit']
        widgets = {
            'bloodgroup': forms.Select(choices=[
                ('A+', 'A+'), ('A-', 'A-'), ('B+', 'B+'), ('B-', 'B-'),
                ('AB+', 'AB+'), ('AB-', 'AB-'), ('O+', 'O+'), ('O-', 'O-')
            ], attrs={'class': 'form-control'}),
            'unit': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'})
        }

    def clean_unit(self):
        unit = self.cleaned_data.get('unit')
        if unit is None or unit < 1:
            raise forms.ValidationError('At least 1 unit of blood must be requested.')
        if unit > 50:
            raise forms.ValidationError('Cannot request more than 50 units at once.')
        return unit

    def clean_patient_age(self):
        age = self.cleaned_data.get('patient_age')
        if age is not None and (age < 0 or age > 120):
            raise forms.ValidationError('Please enter a valid age (0-120).')
        return age


class FeedbackForm(forms.ModelForm):
    class Meta:
        model = models.Feedback
        fields = [
            'display_name',
            'feedback_for',
            'rating',
            'message',
            'image1',
            'image2',
            'image3',
        ]
        widgets = {
            'display_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Your name (optional)'}),
            'feedback_for': forms.Select(attrs={'class': 'form-control'}),
            'rating': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '5'}),
            'message': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Share your experience...'}),
        }

    def clean_rating(self):
        rating = int(self.cleaned_data.get('rating') or 0)
        if rating < 1 or rating > 5:
            raise forms.ValidationError('Rating must be between 1 and 5.')
        return rating


class AdminFeedbackModerationForm(forms.ModelForm):
    class Meta:
        model = models.Feedback
        fields = ['is_public', 'admin_reaction', 'admin_reply']
        widgets = {
            'admin_reaction': forms.Select(attrs={'class': 'form-control'}),
            'admin_reply': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Reply to this feedback...'}),
        }
