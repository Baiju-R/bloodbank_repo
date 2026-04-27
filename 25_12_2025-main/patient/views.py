import logging
import json

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib import messages
from django.db.models import Sum
from django.core.serializers.json import DjangoJSONEncoder

from blood.models import BloodRequest
from blood.forms import FeedbackForm
from blood.models import Feedback, InAppNotification
from blood.services import sms as sms_service
from .forms import PatientUserForm, PatientForm
from .models import Patient

logger = logging.getLogger(__name__)

def patientsignup_view(request):
    userForm = PatientUserForm()
    patientForm = PatientForm()
    mydict = {'userForm': userForm, 'patientForm': patientForm}
    
    # Check if coming from request-blood redirect
    from_request = request.GET.get('from_request', False)
    
    if request.method == 'POST':
        userForm = PatientUserForm(request.POST)
        patientForm = PatientForm(request.POST, request.FILES)
        
        # Debug form data
        logger.debug("Patient signup POST keys: %s", list(request.POST.keys()))
        logger.debug("Patient signup user form valid: %s", userForm.is_valid())
        logger.debug("Patient signup patient form valid: %s", patientForm.is_valid())
        
        if userForm.errors:
            logger.debug("Patient signup user form errors: %s", userForm.errors)
        if patientForm.errors:
            logger.debug("Patient signup patient form errors: %s", patientForm.errors)

        # Doctor prescription is mandatory at signup
        has_prescription = bool(request.FILES.get('doctor_prescription'))
        if not has_prescription:
            messages.error(request, 'Doctor prescription is mandatory. Please upload your doctor\'s prescription.')
            mydict.update({'userForm': userForm, 'patientForm': patientForm, 'from_request': from_request})
            return render(request, 'patient/patientsignup.html', context=mydict)
        
        if userForm.is_valid() and patientForm.is_valid():
            try:
                # Create user (inactive until admin approves)
                user = userForm.save(commit=False)
                user.set_password(user.password)
                user.is_active = False  # Account inactive until admin approval
                user.save()
                
                # Create patient (not approved yet)
                patient = patientForm.save(commit=False)
                patient.user = user
                patient.is_approved = False
                patient.save()
                
                # Add to patient group
                my_patient_group, created = Group.objects.get_or_create(name='PATIENT')
                my_patient_group.user_set.add(user)
                
                # Registration notification (visible after approval)
                try:
                    InAppNotification.objects.create(
                        patient=patient,
                        title='Registration Received',
                        message=(
                            f"Hello {user.first_name}, your patient registration has been received. "
                            "Your account is pending admin approval. You will receive a notification "
                            "once your account is approved."
                        ),
                    )
                except Exception:
                    logger.exception("Failed to create registration notification for patient %s", user.username)

                messages.success(
                    request,
                    'Registration submitted successfully! Your account is pending admin approval. '
                    'You will be notified once approved.'
                )
                return redirect('patient-pending-approval')
                    
            except Exception as e:
                logger.exception("Error during patient signup")
                messages.error(request, f'Error creating account: {str(e)}')
        else:
            # Form validation failed
            error_messages = []
            
            # Collect user form errors
            for field, errors in userForm.errors.items():
                for error in errors:
                    error_messages.append(f"{field.replace('_', ' ').title()}: {error}")
            
            # Collect patient form errors
            for field, errors in patientForm.errors.items():
                for error in errors:
                    error_messages.append(f"{field.replace('_', ' ').title()}: {error}")
            
            for error in error_messages:
                messages.error(request, error)
    
    mydict['from_request'] = from_request
    return render(request, 'patient/patientsignup.html', context=mydict)


def patient_pending_approval_view(request):
    """Show a waiting page for patients whose accounts are pending admin approval."""
    return render(request, 'patient/pending_approval.html')

def patientlogin_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        
        logger.debug("Patient login attempt - Username: %s", username)

        # Check if user exists but is inactive (pending approval)
        try:
            from django.contrib.auth.models import User as AuthUser
            check_user = AuthUser.objects.get(username=username)
            if not check_user.is_active and check_user.groups.filter(name='PATIENT').exists():
                try:
                    patient_check = Patient.objects.get(user=check_user)
                    if not patient_check.is_approved:
                        messages.warning(
                            request,
                            'Your account is pending admin approval. Please wait for approval before logging in.'
                        )
                        return render(request, 'patient/patientlogin.html')
                except Patient.DoesNotExist:
                    pass
        except AuthUser.DoesNotExist:
            pass
        
        user = authenticate(request, username=username, password=password)
        if user is not None:
            logger.debug("User authenticated: %s", user.username)
            logger.debug("User groups: %s", [g.name for g in user.groups.all()])
            
            if user.groups.filter(name='PATIENT').exists():
                # Check if approved
                try:
                    patient_obj = Patient.objects.get(user=user)
                    if not patient_obj.is_approved:
                        messages.warning(
                            request,
                            'Your account is pending admin approval. Please wait for approval before logging in.'
                        )
                        return render(request, 'patient/patientlogin.html')
                except Patient.DoesNotExist:
                    pass

                login(request, user)
                messages.success(request, f'Welcome back, {user.first_name}!')
                return redirect('patient-dashboard')
            else:
                messages.error(request, 'This account is not registered as a patient.')
        else:
            logger.debug("Authentication failed")
            messages.error(request, 'Invalid username or password.')
    
    return render(request, 'patient/patientlogin.html')

@login_required
def patient_dashboard_view(request):
    if not request.user.groups.filter(name='PATIENT').exists():
        messages.error(request, 'Access denied. Patient account required.')
        return redirect('patientlogin')
    
    try:
        patient = Patient.objects.get(user=request.user)
        requests = BloodRequest.objects.filter(patient=patient)
        
        # Calculate detailed statistics
        total_requests = requests.count()
        approved_requests = requests.filter(status='Approved').count()
        pending_requests = requests.filter(status='Pending').count()
        rejected_requests = requests.filter(status='Rejected').count()
        
        # Calculate total units requested and approved
        total_units_requested = requests.aggregate(Sum('unit'))
        total_units_requested = total_units_requested['unit__sum'] if total_units_requested['unit__sum'] else 0
        
        approved_units = requests.filter(status='Approved').aggregate(Sum('unit'))
        total_approved_units = approved_units['unit__sum'] if approved_units['unit__sum'] else 0
        
        # Get recent requests
        recent_requests = requests.order_by('-date')[:5]
        recent_feedbacks = Feedback.objects.filter(patient=patient).order_by('-created_at')[:5]
        from blood.models import InAppNotification, VerificationBadge
        verification_badge = VerificationBadge.objects.filter(patient=patient).order_by('-verified_at', '-id').first()
        notifications = InAppNotification.objects.filter(patient=patient).order_by('-created_at')[:5]
        
        context = {
            'patient': patient,
            'total_requests': total_requests,
            'approved_requests': approved_requests,
            'pending_requests': pending_requests,
            'rejected_requests': rejected_requests,
            'total_units_requested': total_units_requested,
            'total_approved_units': total_approved_units,
            'recent_requests': recent_requests,
            'recent_feedbacks': recent_feedbacks,
            'verification_badge': verification_badge,
            'notifications': notifications,
        }
    except Patient.DoesNotExist:
        messages.error(request, 'Patient profile not found. Please contact support.')
        context = {
            'patient': None,
            'total_requests': 0,
            'approved_requests': 0,
            'pending_requests': 0,
            'rejected_requests': 0,
            'total_units_requested': 0,
            'total_approved_units': 0,
            'recent_requests': [],
            'verification_badge': None,
            'notifications': [],
        }
    
    return render(request, 'patient/patient_dashboard.html', context)


@login_required
def patient_nearby_donors_view(request):
    if not request.user.groups.filter(name='PATIENT').exists():
        messages.error(request, 'Access denied. Patient account required.')
        return redirect('patientlogin')

    patient = get_object_or_404(Patient, user=request.user)
    from donor.models import Donor

    latest_zip = (
        BloodRequest.objects.filter(patient=patient)
        .exclude(request_zipcode='')
        .order_by('-date', '-id')
        .values_list('request_zipcode', flat=True)
        .first()
    )
    zipcode = (request.GET.get('zipcode') or latest_zip or '').strip()
    bloodgroup = (request.GET.get('bloodgroup') or '').strip()

    donor_qs = Donor.objects.filter(is_available=True).select_related('user')
    if bloodgroup:
        donor_qs = donor_qs.filter(bloodgroup=bloodgroup)
    if zipcode:
        donor_qs = donor_qs.filter(zipcode=zipcode)

    donors = []
    for donor in donor_qs[:120]:
        distance_km = None
        eta = 'N/A'
        if donor.latitude is not None and donor.longitude is not None and zipcode and donor.zipcode and donor.zipcode == zipcode:
            distance_km = 0
            eta = '~15 min (same area)'
        donors.append({
            'id': donor.id,
            'name': donor.get_name,
            'bloodgroup': donor.bloodgroup,
            'mobile': donor.mobile,
            'address': donor.address,
            'zipcode': donor.zipcode,
            'latitude': float(donor.latitude) if donor.latitude is not None else None,
            'longitude': float(donor.longitude) if donor.longitude is not None else None,
            'location_verified': donor.location_verified,
            'distance_km': distance_km,
            'eta': eta,
        })

    if donors and donors[0]['latitude'] is not None:
        map_center = {'lat': donors[0]['latitude'], 'lng': donors[0]['longitude']}
    else:
        map_center = {'lat': 20.5937, 'lng': 78.9629}

    context = {
        'patient': patient,
        'zipcode': zipcode,
        'bloodgroup': bloodgroup,
        'donors': donors,
        'donor_map_json': json.dumps(donors, cls=DjangoJSONEncoder),
        'blood_groups': ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'],
        'map_center': map_center,
    }
    return render(request, 'patient/nearby_donors.html', context)


@login_required
def patient_feedback_create_view(request):
    if not request.user.groups.filter(name='PATIENT').exists():
        messages.error(request, 'Access denied. Patient account required.')
        return redirect('patientlogin')

    patient = get_object_or_404(Patient, user=request.user)

    form = FeedbackForm()
    if request.method == 'POST':
        form = FeedbackForm(request.POST, request.FILES)
        if form.is_valid():
            feedback = form.save(commit=False)
            feedback.author_type = Feedback.AUTHOR_PATIENT
            feedback.patient = patient
            feedback.donor = None
            feedback.display_name = ''
            feedback.is_public = True
            feedback.save()
            messages.success(request, 'Thanks! Your feedback has been submitted.')
            return redirect('patient-dashboard')
        messages.error(request, 'Please fix the errors in the feedback form.')

    return render(request, 'patient/feedback_form.html', {'form': form})

@login_required
def patient_request_view(request):
    if not request.user.groups.filter(name='PATIENT').exists():
        messages.error(request, 'Access denied. Patient account required.')
        return redirect('patientlogin')
    
    try:
        patient = Patient.objects.get(user=request.user)
    except Patient.DoesNotExist:
        messages.error(request, 'Patient profile not found. Please contact support.')
        return redirect('patient-dashboard')

    form_data = {
        'patient_name': '',
        'patient_age': '',
        'reason': '',
        'bloodgroup': '',
        'unit': '',
        'request_zipcode': '',
        'is_urgent': False,
    }
    
    if request.method == 'POST':
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
        
        if not bloodgroup:
            errors.append('Blood group is required.')
        elif bloodgroup not in ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']:
            errors.append('Please select a valid blood group.')
        
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

        # Doctor prescription is mandatory for every request (including emergency)
        prescription_file = request.FILES.get('doctor_prescription')
        if not prescription_file:
            errors.append('Doctor prescription is mandatory for every blood request (including emergency requests).')
        
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, 'patient/makerequest.html', {'patient': patient, 'form_data': form_data})
        
        # Create blood request
        try:
            blood_request = BloodRequest.objects.create(
                patient=patient,
                patient_name=patient_name,
                patient_age=patient_age,
                reason=reason,
                bloodgroup=bloodgroup,
                unit=unit,
                status='Pending',
                doctor_prescription=prescription_file,
                is_urgent=is_urgent,
                request_zipcode=request_zipcode,
            )

            from blood.models import InAppNotification
            InAppNotification.objects.create(
                patient=patient,
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
                success_msg += ' Our coordinators will fast-track this request and reach out shortly.'
            messages.success(request, success_msg)

            if is_urgent:
                try:
                    from blood import tasks as sms_tasks

                    sms_tasks.send_urgent_alerts.delay(blood_request.pk, contact_number=patient.mobile)
                except Exception as alert_error:  # pragma: no cover
                    logger.error(
                        "Failed to enqueue urgent alerts for patient request %s: %s; falling back to synchronous send",
                        blood_request.id,
                        alert_error,
                    )
                    try:
                        sms_service.notify_matched_donors(blood_request, contact_number=patient.mobile)
                    except Exception as fallback_error:  # pragma: no cover
                        logger.error(
                            "Failed to dispatch SNS alert for patient request %s: %s",
                            blood_request.id,
                            fallback_error,
                        )

                try:
                    from blood import tasks as sms_tasks

                    sms_tasks.send_requester_confirmation_sms.delay(blood_request.pk, contact_number=patient.mobile)
                except Exception as confirm_error:  # pragma: no cover
                    logger.error(
                        "Failed to enqueue requester confirmation for patient request %s: %s; falling back to synchronous send",
                        blood_request.id,
                        confirm_error,
                    )
                    try:
                        sms_service.send_requester_confirmation(blood_request, patient.mobile)
                    except Exception as fallback_error:  # pragma: no cover
                        logger.error(
                            "Failed to send requester confirmation for patient request %s: %s",
                            blood_request.id,
                            fallback_error,
                        )
            return redirect('my-request')
            
        except Exception as e:
            print(f"Error creating blood request: {str(e)}")
            messages.error(request, f'Error submitting request: {str(e)}')
    
    return render(request, 'patient/makerequest.html', {'patient': patient, 'form_data': form_data})

@login_required
def patient_request_history_view(request):
    if not request.user.groups.filter(name='PATIENT').exists():
        messages.error(request, 'Access denied. Patient account required.')
        return redirect('patientlogin')
    
    try:
        patient = Patient.objects.get(user=request.user)
        requests = BloodRequest.objects.filter(patient=patient).order_by('-date')
        
        # Calculate statistics
        total_requests = requests.count()
        approved_count = requests.filter(status='Approved').count()
        pending_count = requests.filter(status='Pending').count()
        rejected_count = requests.filter(status='Rejected').count()
        
        # Calculate units
        total_units = requests.aggregate(Sum('unit'))
        total_units_requested = total_units['unit__sum'] if total_units['unit__sum'] else 0
        
        approved_units = requests.filter(status='Approved').aggregate(Sum('unit'))
        total_approved_units = approved_units['unit__sum'] if approved_units['unit__sum'] else 0

        # ── Enrich with queue position & availability ────────────────────
        from blood.models import Stock
        from django.utils import timezone as tz
        today = tz.now().date()
        stock_cache = {s.bloodgroup: s.unit for s in Stock.objects.all()}

        # Queue position: count how many pending requests for this blood group
        # exist before this patient's pending request
        pending_all_by_bg = {}  # blood_group → list of pending request IDs in date order
        for bg_val in stock_cache.keys():
            pending_all_by_bg[bg_val] = list(
                BloodRequest.objects.filter(status='Pending', bloodgroup=bg_val)
                .order_by('date', 'id')
                .values_list('id', flat=True)
            )

        for br in requests:
            bg = br.bloodgroup
            stock_units = stock_cache.get(bg, 0)
            br.days_old = (today - br.date).days if br.date else 0

            # Queue position for pending requests
            if br.status == 'Pending':
                queue_ids = pending_all_by_bg.get(bg, [])
                try:
                    br.queue_position = queue_ids.index(br.id) + 1
                except ValueError:
                    br.queue_position = len(queue_ids) + 1
                br.queue_total = len(queue_ids)
            else:
                br.queue_position = None
                br.queue_total = None

            # Availability indicator
            if stock_units == 0:
                br.avail_label, br.avail_css = 'Out of Stock', 'avail-empty'
            elif stock_units < br.unit:
                br.avail_label, br.avail_css = 'Limited', 'avail-limited'
            elif stock_units < br.unit * 3:
                br.avail_label, br.avail_css = 'Moderate', 'avail-moderate'
            else:
                br.avail_label, br.avail_css = 'In Stock', 'avail-ok'

            # Confidence score — higher when stock is ample and queue position is low
            if br.status == 'Pending' and br.queue_position:
                conf_base = min(100, max(10, int((stock_units / max(br.unit, 1)) * 40)))
                conf_queue = max(0, 30 - (br.queue_position - 1) * 8)
                br.confidence = min(95, conf_base + conf_queue)
                br.confidence_css = 'conf-high' if br.confidence >= 70 else ('conf-mid' if br.confidence >= 40 else 'conf-low')
            elif br.status == 'Approved':
                br.confidence = 100
                br.confidence_css = 'conf-done'
            else:
                br.confidence = 0
                br.confidence_css = 'conf-na'

        context = {
            'requests': requests,
            'total_requests': total_requests,
            'approved_count': approved_count,
            'pending_count': pending_count,
            'rejected_count': rejected_count,
            'total_units_requested': total_units_requested,
            'total_approved_units': total_approved_units,
        }
    except Patient.DoesNotExist:
        messages.error(request, 'Patient profile not found. Please contact support.')
        context = {
            'requests': [],
            'total_requests': 0,
            'approved_count': 0,
            'pending_count': 0,
            'rejected_count': 0,
            'total_units_requested': 0,
            'total_approved_units': 0,
        }
    
    return render(request, 'patient/my_request.html', context)
