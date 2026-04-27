import logging
from datetime import datetime, time, timedelta

from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib import messages
from django.conf import settings
from django.db.models import Sum, Q, Count, F
from django.views.decorators.http import require_POST
from django.utils import timezone

from blood.services import sms as sms_service
from blood.forms import FeedbackForm
from blood.models import Feedback, InAppNotification
from .forms import DonorUserForm, DonorForm, MedicalReportForm
from .models import Donor, BloodDonate, MedicalReport

logger = logging.getLogger(__name__)


def _ensure_default_appointment_slots_exist(*, created_by=None) -> int:
    """Create a few upcoming appointment slots when none are available.

    Fresh installs/demo databases often have zero slots, which makes the booking UI
    show "No active slots". This helper seeds a small number of near-term slots.

    Controlled by `settings.AUTO_SEED_APPOINTMENT_SLOTS` (default: settings.DEBUG).
    """

    from blood.models import DonationAppointmentSlot

    allow_seed = getattr(settings, 'AUTO_SEED_APPOINTMENT_SLOTS', True)
    if not allow_seed:
        return 0

    now = timezone.now()
    if DonationAppointmentSlot.objects.filter(is_active=True, end_at__gte=now).exists():
        return 0

    # Avoid unbounded growth if slots exist but are all inactive/expired.
    if DonationAppointmentSlot.objects.count() >= 80:
        return 0

    tz = timezone.get_current_timezone()
    local_now = timezone.localtime(now)
    start_date = local_now.date()
    if local_now.hour >= 18:
        start_date = start_date + timedelta(days=1)

    created = 0
    # Two slots per day for the next 3 days.
    for day_offset in range(0, 3):
        day = start_date + timedelta(days=day_offset)
        for hour in (10, 14):
            naive_start = datetime.combine(day, time(hour=hour, minute=0))
            start_at = timezone.make_aware(naive_start, tz)
            end_at = start_at + timedelta(hours=1)

            DonationAppointmentSlot.objects.create(
                start_at=start_at,
                end_at=end_at,
                capacity=10,
                is_active=True,
                notes='Auto-generated slot (edit in Admin > Appointments)',
                created_by=created_by if getattr(created_by, 'is_authenticated', False) else None,
            )
            created += 1

    return created


def _donor_eligibility_summary(donor: Donor) -> dict:
    reasons = []
    today = timezone.now().date()

    if not donor.is_available:
        reasons.append('You are currently marked unavailable.')

    # Medical report check
    if not donor.is_medical_report_valid:
        reasons.append('Medical health report is expired or missing. Please upload a new report.')
    else:
        days_left = donor.medical_report_days_remaining
        if days_left is not None and days_left <= 14:
            reasons.append(f'Medical report expires in {days_left} day(s). Please renew soon.')

    if donor.age_years is not None and (donor.age_years < 18 or donor.age_years > 65):
        reasons.append('Age must be between 18 and 65 for donation.')

    if donor.weight_kg is not None and donor.weight_kg < 45:
        reasons.append('Minimum weight requirement is 45 kg.')

    if donor.hemoglobin_g_dl is not None and float(donor.hemoglobin_g_dl) < 12.5:
        reasons.append('Hemoglobin should be at least 12.5 g/dL.')

    if donor.has_chronic_disease:
        reasons.append('Chronic disease requires manual medical review.')

    next_date = donor.next_eligible_donation_date
    if next_date and next_date > today:
        remaining = (next_date - today).days
        reasons.append(f'Recovery period active. Eligible again in {remaining} day(s).')

    eligible = len(reasons) == 0
    return {
        'is_eligible': eligible,
        'label': 'Eligible to Donate' if eligible else 'Not Eligible Yet',
        'reasons': reasons,
        'next_eligible_date': next_date,
    }


@login_required
@require_POST
def donor_set_availability_view(request):
    if not request.user.groups.filter(name='DONOR').exists():
        messages.error(request, 'Access denied. Donor account required.')
        return redirect('donorlogin')

    donor = get_object_or_404(Donor, user=request.user)
    raw = (request.POST.get('available') or '').strip().lower()
    available = raw in {'1', 'true', 'yes', 'on'}
    donor.mark_availability(available)

    if available:
        messages.success(request, 'You are now marked as available for donation requests.')
    else:
        messages.warning(request, 'You are now marked as not available. Admins will not contact you for requests.')

    return redirect('donor-dashboard')

def donorsignup_view(request):
    userForm = DonorUserForm()
    donorForm = DonorForm()
    reportForm = MedicalReportForm()
    mydict = {'userForm': userForm, 'donorForm': donorForm, 'reportForm': reportForm}

    if request.method == 'POST':
        userForm = DonorUserForm(request.POST)
        donorForm = DonorForm(request.POST, request.FILES)
        reportForm = MedicalReportForm(request.POST, request.FILES)

        logger.debug("Donor signup POST keys: %s", list(request.POST.keys()))
        logger.debug("Donor signup user form valid: %s", userForm.is_valid())
        logger.debug("Donor signup donor form valid: %s", donorForm.is_valid())
        logger.debug("Donor signup report form valid: %s", reportForm.is_valid())

        if userForm.errors:
            logger.debug("Donor signup user form errors: %s", userForm.errors)
        if donorForm.errors:
            logger.debug("Donor signup donor form errors: %s", donorForm.errors)
        if reportForm.errors:
            logger.debug("Donor signup report form errors: %s", reportForm.errors)

        # Medical report document is mandatory at signup
        has_report = bool(request.FILES.get('document'))
        if not has_report:
            messages.error(request, 'Medical health report is mandatory. Please upload your recent medical report.')
            mydict.update({'userForm': userForm, 'donorForm': donorForm, 'reportForm': reportForm})
            return render(request, 'donor/donorsignup.html', context=mydict)

        if userForm.is_valid() and donorForm.is_valid() and reportForm.is_valid():
            try:
                # Create user (inactive until admin approves)
                user = userForm.save(commit=False)
                user.set_password(user.password)
                user.is_active = False  # Account inactive until admin approval
                user.save()

                # Create donor (not approved yet)
                donor = donorForm.save(commit=False)
                donor.user = user
                donor.is_approved = False
                donor.save()

                # Save the medical report
                report = reportForm.save(commit=False)
                report.donor = donor
                report.document_name = request.FILES['document'].name
                report.save()

                # Add to donor group
                my_donor_group, created = Group.objects.get_or_create(name='DONOR')
                my_donor_group.user_set.add(user)

                # Notification for donor (will be visible after approval)
                try:
                    InAppNotification.objects.create(
                        donor=donor,
                        title='Registration Received',
                        message=(
                            f"Hello {user.first_name}, your donor registration has been received. "
                            "Your account is pending admin approval. You will receive a notification "
                            "once your account is approved."
                        ),
                    )
                except Exception:
                    logger.exception("Failed to create registration notification for donor %s", user.username)

                messages.success(
                    request,
                    'Registration submitted successfully! Your account is pending admin approval. '
                    'You will be notified once approved.'
                )
                return redirect('donor-pending-approval')

            except Exception as e:
                logger.exception("Error during donor signup")
                messages.error(request, f'Error creating account: {str(e)}')
        else:
            # Form validation failed
            error_messages = []

            # Collect user form errors
            for field, errors in userForm.errors.items():
                for error in errors:
                    error_messages.append(f"{field.replace('_', ' ').title()}: {error}")

            # Collect donor form errors
            for field, errors in donorForm.errors.items():
                for error in errors:
                    error_messages.append(f"{field.replace('_', ' ').title()}: {error}")

            # Collect report form errors
            for field, errors in reportForm.errors.items():
                for error in errors:
                    error_messages.append(f"Medical Report {field.replace('_', ' ').title()}: {error}")

            for error in error_messages:
                messages.error(request, error)

    mydict.update({'userForm': userForm, 'donorForm': donorForm, 'reportForm': reportForm})
    return render(request, 'donor/donorsignup.html', context=mydict)


def donor_pending_approval_view(request):
    """Show a waiting page for donors whose accounts are pending admin approval."""
    return render(request, 'donor/pending_approval.html')

def donorlogin_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        logger.debug("Donor login attempt - Username: %s", username)

        # Check if user exists but is inactive (pending approval)
        try:
            check_user = User.objects.get(username=username)
            if not check_user.is_active and check_user.groups.filter(name='DONOR').exists():
                try:
                    donor = Donor.objects.get(user=check_user)
                    if not donor.is_approved:
                        messages.warning(
                            request,
                            'Your account is pending admin approval. Please wait for approval before logging in.'
                        )
                        return render(request, 'donor/donorlogin.html')
                except Donor.DoesNotExist:
                    pass
        except User.DoesNotExist:
            pass

        user = authenticate(request, username=username, password=password)
        if user is not None:
            logger.debug("User authenticated: %s", user.username)
            logger.debug("User groups: %s", [g.name for g in user.groups.all()])

            if user.groups.filter(name='DONOR').exists():
                # Check if approved
                try:
                    donor = Donor.objects.get(user=user)
                    if not donor.is_approved:
                        messages.warning(
                            request,
                            'Your account is pending admin approval. Please wait for approval before logging in.'
                        )
                        return render(request, 'donor/donorlogin.html')
                except Donor.DoesNotExist:
                    pass

                login(request, user)
                messages.success(request, f'Welcome back, {user.first_name}!')
                return redirect('donor-dashboard')
            else:
                messages.error(request, 'This account is not registered as a donor.')
        else:
            logger.debug("Authentication failed")
            messages.error(request, 'Invalid username or password.')

    return render(request, 'donor/donorlogin.html')

@login_required
def donor_dashboard_view(request):
    logger.debug("Donor dashboard accessed by user: %s", request.user.username)
    logger.debug("User is authenticated: %s", request.user.is_authenticated)
    logger.debug("User groups: %s", [g.name for g in request.user.groups.all()])
    
    if not request.user.groups.filter(name='DONOR').exists():
        logger.debug("User not in DONOR group, redirecting to login")
        messages.error(request, 'Access denied. Donor account required.')
        return redirect('donorlogin')
    
    try:
        donor = Donor.objects.get(user=request.user)
        logger.debug("Donor profile found: %s", donor.get_name)
        
        # Add comprehensive debugging
        donations = BloodDonate.objects.filter(donor=donor)
        logger.debug("Found %s donations for donor", donations.count())
        
        # Get donation statistics
        total_donations = donations.count()
        approved_donations = donations.filter(status='Approved').count()
        pending_donations = donations.filter(status='Pending').count()
        rejected_donations = donations.filter(status='Rejected').count()
        
        # Calculate total units donated (approved only)
        approved_units = donations.filter(status='Approved').aggregate(Sum('unit'))
        total_units_donated = approved_units['unit__sum'] if approved_units['unit__sum'] else 0
        
        # Get blood requests made by this donor
        from blood.models import BloodRequest
        blood_requests = BloodRequest.objects.filter(request_by_donor=donor)
        requestmade = blood_requests.count()
        request_pending = blood_requests.filter(status='Pending').count()
        request_approved = blood_requests.filter(status='Approved').count()
        request_rejected = blood_requests.filter(status='Rejected').count()
        
        # Get recent activities
        recent_donations = donations.order_by('-date')[:5]
        recent_requests = blood_requests.order_by('-date')[:5]
        recent_feedbacks = Feedback.objects.filter(donor=donor).order_by('-created_at')[:5]
        from blood.models import InAppNotification, VerificationBadge
        notifications = InAppNotification.objects.filter(donor=donor).order_by('-created_at')[:5]
        badge = VerificationBadge.objects.filter(donor=donor).order_by('-verified_at', '-id').first()

        eligibility = _donor_eligibility_summary(donor)

        approved_donation_qs = donations.filter(status='Approved').order_by('-date', '-id')
        donation_dates = [entry.date for entry in approved_donation_qs[:12]]
        streak = 0
        previous = None
        for donated_on in donation_dates:
            if previous is None:
                streak = 1
                previous = donated_on
                continue
            if (previous - donated_on).days <= 90:
                streak += 1
                previous = donated_on
            else:
                break

        milestones = [1, 3, 5, 10, 20]
        next_milestone = next((m for m in milestones if approved_donations < m), None)
        city_leaders = (
            Donor.objects.exclude(zipcode='')
            .values('zipcode')
            .annotate(total_units=Sum('blooddonate__unit', filter=Q(blooddonate__status='Approved')))
            .order_by('-total_units')[:5]
        )
        
        logger.debug("Donor statistics: Donations=%s, Requests=%s", total_donations, requestmade)
        
        context = {
            'donor': donor,
            'total_donations': total_donations,
            'approved_donations': approved_donations,
            'pending_donations': pending_donations,
            'rejected_donations': rejected_donations,
            'total_units_donated': total_units_donated,
            'recent_donations': recent_donations,
            'requestmade': requestmade,
            'request_pending': request_pending,
            'request_approved': request_approved,
            'request_rejected': request_rejected,
            'recent_requests': recent_requests,
            'recent_feedbacks': recent_feedbacks,
            'eligibility': eligibility,
            'verification_badge': badge,
            'notifications': notifications,
            'donation_streak': streak,
            'next_milestone': next_milestone,
            'lives_helped': max(1, total_units_donated // 350) if total_units_donated else 0,
            'city_leaders': city_leaders,
            # Medical report status
            'is_medical_report_valid': donor.is_medical_report_valid,
            'medical_report_expiry': donor.medical_report_expiry_date,
            'medical_report_days_remaining': donor.medical_report_days_remaining,
            'latest_medical_report': donor.latest_medical_report,
        }
        
    except Donor.DoesNotExist:
        logger.warning("Donor profile not found for user: %s", request.user.username)
        messages.error(request, 'Donor profile not found. Please contact support.')
        context = {
            'donor': None,
            'total_donations': 0,
            'approved_donations': 0,
            'pending_donations': 0,
            'rejected_donations': 0,
            'total_units_donated': 0,
            'requestmade': 0,
            'request_pending': 0,
            'request_approved': 0,
            'request_rejected': 0,
            'recent_donations': [],
            'recent_requests': [],
            'eligibility': {'is_eligible': False, 'label': 'Profile Missing', 'reasons': ['Profile unavailable.'], 'next_eligible_date': None},
            'verification_badge': None,
            'notifications': [],
            'donation_streak': 0,
            'next_milestone': None,
            'lives_helped': 0,
            'city_leaders': [],
        }
    except Exception as e:
        logger.exception("Error in donor dashboard")
        messages.error(request, f'Dashboard error: {str(e)}')
        context = {
            'donor': None,
            'total_donations': 0,
            'approved_donations': 0,
            'pending_donations': 0,
            'rejected_donations': 0,
            'total_units_donated': 0,
            'requestmade': 0,
            'request_pending': 0,
            'request_approved': 0,
            'request_rejected': 0,
            'recent_donations': [],
            'recent_requests': [],
            'eligibility': {'is_eligible': False, 'label': 'Unavailable', 'reasons': ['Could not evaluate eligibility.'], 'next_eligible_date': None},
            'verification_badge': None,
            'notifications': [],
            'donation_streak': 0,
            'next_milestone': None,
            'lives_helped': 0,
            'city_leaders': [],
        }
    
    logger.debug("Rendering donor dashboard with context keys: %s", list(context.keys()))
    return render(request, 'donor/donor_dashboard.html', context)


@login_required
def donor_feedback_create_view(request):
    if not request.user.groups.filter(name='DONOR').exists():
        messages.error(request, 'Access denied. Donor account required.')
        return redirect('donorlogin')

    donor = get_object_or_404(Donor, user=request.user)

    form = FeedbackForm()
    if request.method == 'POST':
        form = FeedbackForm(request.POST, request.FILES)
        if form.is_valid():
            feedback = form.save(commit=False)
            feedback.author_type = Feedback.AUTHOR_DONOR
            feedback.donor = donor
            feedback.patient = None
            feedback.display_name = ''
            feedback.is_public = True
            feedback.save()
            messages.success(request, 'Thanks! Your feedback has been submitted.')
            return redirect('donor-dashboard')
        messages.error(request, 'Please fix the errors in the feedback form.')

    return render(request, 'donor/feedback_form.html', {'form': form})

@login_required
def donate_blood_view(request):
    if not request.user.groups.filter(name='DONOR').exists():
        messages.error(request, 'Access denied. Donor account required.')
        return redirect('donorlogin')
    
    try:
        donor = Donor.objects.get(user=request.user)
    except Donor.DoesNotExist:
        messages.error(request, 'Donor profile not found. Please contact support.')
        return redirect('donor-dashboard')
    
    if request.method == 'POST':
        bloodgroup = request.POST.get('bloodgroup', '').strip()
        unit = request.POST.get('unit', '').strip()
        disease = request.POST.get('disease', 'None').strip()
        age = request.POST.get('age', '').strip()
        
        # Enhanced validation
        errors = []
        
        if not bloodgroup or bloodgroup == 'Choose Blood Group':
            errors.append('Please select your blood group.')
        
        if not unit:
            errors.append('Unit amount is required.')
        else:
            try:
                unit_val = int(unit)
                if unit_val < 100 or unit_val > 500:
                    errors.append('Unit amount must be between 100ml and 500ml.')
            except ValueError:
                errors.append('Please enter a valid unit amount.')
        
        if not age:
            errors.append('Age is required.')
        else:
            try:
                age_val = int(age)
                if age_val < 18 or age_val > 65:
                    errors.append('Age must be between 18 and 65 years for blood donation.')
            except ValueError:
                errors.append('Please enter a valid age.')
        
        if not disease:
            disease = 'None'
        
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, 'donor/donate_blood.html', {'donor': donor})
        
        try:
            donation = BloodDonate.objects.create(
                donor=donor,
                disease=disease,
                age=int(age),
                bloodgroup=bloodgroup,
                unit=int(unit),
                status='Pending'
            )

            from blood.models import InAppNotification
            InAppNotification.objects.create(
                donor=donor,
                title='Donation Request Submitted',
                message=(
                    f'Your donation request #{donation.id} for {int(unit)}ml '
                    f'({bloodgroup}) is pending admin review.'
                ),
            )
            
            messages.success(request, f'Donation request submitted successfully! {unit}ml of {bloodgroup} blood donation pending approval.')
            return redirect('donor-history')
            
        except Exception as e:
            logger.exception("Error creating blood donation")
            messages.error(request, f'Error submitting donation: {str(e)}')
    
    return render(request, 'donor/donate_blood.html', {'donor': donor})

@login_required
def donor_history_view(request):
    if not request.user.groups.filter(name='DONOR').exists():
        messages.error(request, 'Access denied. Donor account required.')
        return redirect('donorlogin')
    
    try:
        donor = Donor.objects.get(user=request.user)
        donations = BloodDonate.objects.filter(donor=donor).order_by('-date')
        
        # Calculate statistics
        total_donations = donations.count()
        approved_count = donations.filter(status='Approved').count()
        pending_count = donations.filter(status='Pending').count()
        rejected_count = donations.filter(status='Rejected').count()
        
        # Calculate total units (approved only)
        approved_units = donations.filter(status='Approved').aggregate(Sum('unit'))
        total_approved_units = approved_units['unit__sum'] if approved_units['unit__sum'] else 0
        
        context = {
            'donations': donations,
            'total_donations': total_donations,
            'approved_count': approved_count,
            'pending_count': pending_count,
            'rejected_count': rejected_count,
            'total_approved_units': total_approved_units,
        }
    except Donor.DoesNotExist:
        messages.error(request, 'Donor profile not found. Please contact support.')
        context = {
            'donations': [],
            'total_donations': 0,
            'approved_count': 0,
            'pending_count': 0,
            'rejected_count': 0,
            'total_approved_units': 0,
        }
    
    return render(request, 'donor/donation_history.html', context)

@login_required
def donor_request_blood_view(request):
    if not request.user.groups.filter(name='DONOR').exists():
        messages.error(request, 'Access denied. Donor account required.')
        return redirect('donorlogin')
    
    try:
        donor = Donor.objects.get(user=request.user)
    except Donor.DoesNotExist:
        messages.error(request, 'Donor profile not found. Please contact support.')
        return redirect('donor-dashboard')
    
    form_data = {
        'patient_name': '',
        'patient_age': '',
        'reason': '',
        'bloodgroup': '',
        'unit': '',
        'request_zipcode': donor.zipcode or '',
        'is_urgent': False,
    }
    
    if request.method == 'POST':
        from blood.models import BloodRequest
        
        form_data.update({
            'patient_name': request.POST.get('patient_name', '').strip(),
            'patient_age': request.POST.get('patient_age', '').strip(),
            'reason': request.POST.get('reason', '').strip(),
            'bloodgroup': request.POST.get('bloodgroup', '').strip(),
            'unit': request.POST.get('unit', '').strip(),
            'request_zipcode': request.POST.get('request_zipcode', '').strip(),
            'is_urgent': request.POST.get('is_urgent') == 'on',
        })
        patient_name = form_data['patient_name']
        patient_age_raw = form_data['patient_age']
        reason = form_data['reason']
        bloodgroup = form_data['bloodgroup']
        unit_raw = form_data['unit']
        request_zipcode = form_data['request_zipcode']
        is_urgent = form_data['is_urgent']
        
        # Enhanced validation
        errors = []
        patient_age = None
        unit = None
        
        if not patient_name:
            errors.append('Patient name is required.')
        elif len(patient_name) < 2:
            errors.append('Patient name must be at least 2 characters.')
        
        if not patient_age_raw:
            errors.append('Patient age is required.')
        else:
            try:
                patient_age = int(patient_age_raw)
                if patient_age < 1 or patient_age > 120:
                    errors.append('Patient age must be between 1 and 120.')
            except ValueError:
                errors.append('Please enter a valid age.')
        
        if not reason:
            errors.append('Reason for blood request is required.')
        elif len(reason) < 10:
            errors.append('Please provide a detailed reason (at least 10 characters).')
        
        if not bloodgroup or bloodgroup == 'Choose Blood Group':
            errors.append('Please select a blood group.')
        
        if not unit_raw:
            errors.append('Unit amount is required.')
        else:
            try:
                unit = int(unit_raw)
                if unit < 100 or unit > 500:
                    errors.append('Unit amount must be between 100ml and 500ml.')
            except ValueError:
                errors.append('Please enter a valid unit amount.')

        if request_zipcode:
            if not request_zipcode.isdigit() or not (4 <= len(request_zipcode) <= 10):
                errors.append('Zip/Postal code must be 4-10 digits.')
        elif is_urgent:
            errors.append('Zip/Postal code is required so admins can triage urgent requests locally.')
        
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, 'donor/makerequest.html', {'donor': donor, 'form_data': form_data})
        
        try:
            blood_request = BloodRequest.objects.create(
                request_by_donor=donor,
                patient_name=patient_name,
                patient_age=patient_age,
                reason=reason,
                bloodgroup=bloodgroup,
                unit=unit,
                status='Pending',
                is_urgent=is_urgent,
                request_zipcode=request_zipcode,
            )

            from blood.models import InAppNotification
            InAppNotification.objects.create(
                donor=donor,
                related_request=blood_request,
                title='Blood Request Submitted',
                message=(
                    f'Your blood request #{blood_request.id} for {unit}ml '
                    f'({bloodgroup}) has been submitted and is pending review.'
                ),
            )
            
            success_msg = (
                f'Blood request submitted successfully! Requested {unit}ml of {bloodgroup} blood for {patient_name}.'
            )
            if is_urgent:
                success_msg += ' Admins will prioritize this request and coordinate follow-ups directly.'
            messages.success(request, success_msg)

            if is_urgent:
                try:
                    from blood import tasks as sms_tasks

                    sms_tasks.send_urgent_alerts.delay(blood_request.pk, contact_number=donor.mobile)
                except Exception as alert_error:  # pragma: no cover - defensive logging only
                    logger.error(
                        "Failed to enqueue urgent alerts for donor request %s: %s; falling back to synchronous send",
                        blood_request.id,
                        alert_error,
                    )
                    try:
                        sms_service.notify_matched_donors(blood_request, contact_number=donor.mobile)
                    except Exception as fallback_error:  # pragma: no cover
                        logger.error(
                            "Failed to dispatch SNS alert for donor request %s: %s",
                            blood_request.id,
                            fallback_error,
                        )

                try:
                    from blood import tasks as sms_tasks

                    sms_tasks.send_requester_confirmation_sms.delay(blood_request.pk, contact_number=donor.mobile)
                except Exception as confirm_error:  # pragma: no cover
                    logger.error(
                        "Failed to enqueue requester confirmation for donor request %s: %s; falling back to synchronous send",
                        blood_request.id,
                        confirm_error,
                    )
                    try:
                        sms_service.send_requester_confirmation(blood_request, donor.mobile)
                    except Exception as fallback_error:  # pragma: no cover
                        logger.error(
                            "Failed to send requester confirmation for donor request %s: %s",
                            blood_request.id,
                            fallback_error,
                        )
            return redirect('donor-request-history')
            
        except Exception as e:
            logger.exception("Error creating blood request")
            messages.error(request, f'Error submitting request: {str(e)}')
    
    return render(request, 'donor/makerequest.html', {'donor': donor, 'form_data': form_data})

@login_required
def donor_request_history_view(request):
    if not request.user.groups.filter(name='DONOR').exists():
        messages.error(request, 'Access denied. Donor account required.')
        return redirect('donorlogin')
    
    try:
        donor = Donor.objects.get(user=request.user)
        from blood.models import BloodRequest, Stock
        requests = BloodRequest.objects.filter(request_by_donor=donor).order_by('-date')
        
        # Calculate statistics
        total_requests = requests.count()
        approved_count = requests.filter(status='Approved').count()
        pending_count = requests.filter(status='Pending').count()
        rejected_count = requests.filter(status='Rejected').count()

        # ── Enrich with urgency + fulfillment data ───────────────────────
        from django.utils import timezone as tz
        import hashlib
        today = tz.now().date()
        stock_cache = {s.bloodgroup: s.unit for s in Stock.objects.all()}
        pending_by_bg = {}  # track queue position per blood group

        for br in requests:
            bg = br.bloodgroup
            # Urgency label (based on request age & is_urgent flag)
            days_old = (today - br.date).days if br.date else 0
            br.days_old = days_old
            if br.status == 'Pending':
                if br.is_urgent or days_old >= 5:
                    br.urgency_label, br.urgency_css = 'Urgent', 'urgency-urgent'
                elif days_old >= 2:
                    br.urgency_label, br.urgency_css = 'Moderate', 'urgency-moderate'
                else:
                    br.urgency_label, br.urgency_css = 'Normal', 'urgency-normal'
            else:
                br.urgency_label, br.urgency_css = '', ''

            # Fulfillment likelihood (stock vs request)
            stock_units = stock_cache.get(bg, 0)
            if br.status == 'Pending':
                if stock_units >= br.unit * 2:
                    br.fulfillment_label, br.fulfillment_css = 'Likely', 'fulfill-likely'
                elif stock_units >= br.unit:
                    br.fulfillment_label, br.fulfillment_css = 'Possible', 'fulfill-possible'
                else:
                    br.fulfillment_label, br.fulfillment_css = 'Unlikely', 'fulfill-unlikely'
            elif br.status == 'Approved':
                br.fulfillment_label, br.fulfillment_css = 'Fulfilled', 'fulfill-done'
            else:
                br.fulfillment_label, br.fulfillment_css = 'Declined', 'fulfill-declined'

        context = {
            'requests': requests,
            'total_requests': total_requests,
            'approved_count': approved_count,
            'pending_count': pending_count,
            'rejected_count': rejected_count,
        }
    except Donor.DoesNotExist:
        messages.error(request, 'Donor profile not found. Please contact support.')
        context = {
            'requests': [],
            'total_requests': 0,
            'approved_count': 0,
            'pending_count': 0,
            'rejected_count': 0,
        }
    
    return render(request, 'donor/request_history.html', context)


@login_required
def donor_appointments_view(request):
    if not request.user.groups.filter(name='DONOR').exists():
        messages.error(request, 'Access denied. Donor account required.')
        return redirect('donorlogin')

    donor = get_object_or_404(Donor, user=request.user)
    from blood.models import DonationAppointmentSlot, DonationAppointment

    reserving_statuses = {
        DonationAppointment.STATUS_PENDING,
        DonationAppointment.STATUS_APPROVED,
        DonationAppointment.STATUS_RESCHEDULED,
    }

    # Ensure fresh/demo installs have something to book.
    try:
        _ensure_default_appointment_slots_exist()
    except Exception as exc:  # pragma: no cover - non-blocking
        logger.warning("Failed to auto-seed appointment slots: %s", exc)

    if request.method == 'POST':
        slot_id = (request.POST.get('slot_id') or '').strip()
        notes = (request.POST.get('notes') or '').strip()[:255]
        if not slot_id.isdigit():
            messages.error(request, 'Please select a valid slot.')
            return redirect('donor-appointments')
        with transaction.atomic():
            slot = (
                DonationAppointmentSlot.objects
                .select_for_update()
                .filter(id=int(slot_id), is_active=True, end_at__gte=timezone.now())
                .first()
            )
            if not slot:
                messages.error(request, 'Selected slot is not available.')
                return redirect('donor-appointments')

            # Prevent duplicate/conflicting bookings for the same donor within the slot window.
            has_conflict = DonationAppointment.objects.filter(
                donor=donor,
                status__in=reserving_statuses,
            ).filter(
                Q(slot=slot)
                | Q(requested_for__gte=slot.start_at, requested_for__lt=slot.end_at)
            ).exists()
            if has_conflict:
                messages.error(request, 'You already have an appointment request for this time slot.')
                return redirect('donor-appointments')

            booked_count = DonationAppointment.objects.filter(
                slot=slot,
                status__in=reserving_statuses,
            ).count()
            if booked_count >= int(slot.capacity or 0):
                messages.error(request, 'Selected slot is full. Please choose another slot.')
                return redirect('donor-appointments')

            DonationAppointment.objects.create(
                donor=donor,
                slot=slot,
                requested_for=slot.start_at,
                notes=notes,
                status=DonationAppointment.STATUS_PENDING,
            )

        from blood.models import InAppNotification
        InAppNotification.objects.create(
            donor=donor,
            title='Appointment Request Submitted',
            message=f'Your appointment request for {slot.start_at:%d %b %Y %H:%M} is pending admin confirmation.',
        )
        messages.success(request, 'Appointment request submitted. Admin will confirm shortly.')
        return redirect('donor-appointments')

    slots_qs = (
        DonationAppointmentSlot.objects
        .filter(is_active=True, end_at__gte=timezone.now())
        .annotate(
            booked_count=Count('donationappointment', filter=Q(donationappointment__status__in=reserving_statuses)),
        )
        .filter(booked_count__lt=F('capacity'))
        .order_by('start_at')[:25]
    )
    slots = list(slots_qs)
    for slot in slots:
        booked = int(getattr(slot, 'booked_count', 0) or 0)
        slot.remaining = max(int(slot.capacity or 0) - booked, 0)
    appointments = DonationAppointment.objects.filter(donor=donor).select_related('slot').order_by('-requested_at')[:25]
    return render(request, 'donor/appointment_booking.html', {'slots': slots, 'appointments': appointments})


@login_required
def donor_upload_medical_report_view(request):
    """Allow donors to upload or renew medical health reports."""
    if not request.user.groups.filter(name='DONOR').exists():
        messages.error(request, 'Access denied. Donor account required.')
        return redirect('donorlogin')

    donor = get_object_or_404(Donor, user=request.user)
    reports = MedicalReport.objects.filter(donor=donor).order_by('-uploaded_at')

    form = MedicalReportForm()
    if request.method == 'POST':
        form = MedicalReportForm(request.POST, request.FILES)
        if form.is_valid():
            report = form.save(commit=False)
            report.donor = donor
            report.document_name = request.FILES['document'].name
            report.save()
            messages.success(
                request,
                'Medical report uploaded successfully! It will be valid for 3 months. '
                'Admin will verify it shortly.'
            )
            return redirect('donor-medical-reports')
        else:
            messages.error(request, 'Please fix the errors in the form.')

    context = {
        'donor': donor,
        'form': form,
        'reports': reports,
        'is_report_valid': donor.is_medical_report_valid,
        'expiry_date': donor.medical_report_expiry_date,
        'days_remaining': donor.medical_report_days_remaining,
    }
    return render(request, 'donor/medical_reports.html', context)