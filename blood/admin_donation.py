from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from donor.models import BloodDonate
from . import models

@login_required
def admin_donation_view(request):
    if not request.user.is_superuser:
        return redirect('adminlogin')
    donations = BloodDonate.objects.all().order_by('-date')
    return render(request, 'blood/admin_donation.html', {'donations': donations})
