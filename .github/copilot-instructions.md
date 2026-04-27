# Copilot Instructions

## Project map
- Django 4.1 project (`bloodbankmanagement/`) with three first-class apps: `blood` (admin + shared services), `donor`, and `patient`; templates live in `templates/<app>/` and share base layouts (`adminbase.html`, `donorbase.html`, `patientbase.html`).
- `blood/models.py` owns the global state (`Stock` rows for each blood group + `BloodRequest` records). `donor/models.py` adds `Donor` + `BloodDonate`; `patient/models.py` adds `Patient`. Keep relationships consistent—`BloodRequest` may attach to either `patient` or `request_by_donor` but never both-null.
- URL routing is centralized in `bloodbankmanagement/urls.py`; app-specific URLs (`donor/urls.py`, `patient/urls.py`) are included under `/donor/` and `/patient/`. Update both the project router and the relevant app router when adding new flows.

## Data & domain rules
- `blood/views.py::home_view` seeds all eight blood groups on first run; do not remove this guard or stock math breaks.
- Stock math happens only when admins approve/ reject workflows: approving a `BloodRequest` deducts units, approving a `BloodDonate` adds units. Never mutate `Stock.unit` elsewhere without mimicking those invariants.
- Status fields (`Pending`, `Approved`, `Rejected`) are hard-coded in dashboards, templates, and filters—introducing new states requires updating all counters (see `admin_dashboard_view`, donor/patient dashboards, and templates).
- Quick requests (`blood/views.py::quick_request_view`) create `BloodRequest` rows without linked user accounts; handle `patient`/`request_by_donor` being `None` everywhere you touch `BloodRequest`.

## Views, templates, and helpers
- All dashboards preload aggregates with `django.db.models.Sum`; keep heavy queries optimized with `select_related` like the existing admin request/donation lists.
- Flash messaging (`django.contrib.messages`) drives user feedback; when adding flows, surface validation errors via messages just like donor/patient signup and request views.
- Custom template filters in `blood/templatetags/math_filters.py` provide `percentage`, `mul`, and `div` helpers used in dashboards—reuse them instead of duplicating math in views.
- Static assets live in `static/` while uploaded avatars go to `media/profile_pic/<role>/`; reference these paths in templates and keep `MEDIA_URL`/`STATIC_URL` consistent with `settings.py`.

## Auth & forms
- Role separation relies on `django.contrib.auth.models.Group` (`DONOR`, `PATIENT`). Every new login-required view must enforce group membership before proceeding (see `donor_dashboard_view`, `patient_dashboard_view`).
- User creation happens through paired `User` + profile forms (e.g., `DonorUserForm` + `DonorForm`); mirror this pattern when creating new actors so passwords are hashed via `set_password` and profile relations stay intact.
- Forms enforce blood group choices via explicit `ChoiceField`s; share those constants to avoid typos that would break stock lookups.

## Developer workflow
- Install deps: `python -m pip install -r requirements.txt` (Django 4.1.13 + `django-widget-tweaks`); a `venv/` is already checked in for reference but shouldn't be committed to.
- Apply schema: `py manage.py makemigrations` + `py manage.py migrate`, then seed an admin superuser with `py manage.py createsuperuser` to access `/admin-dashboard/`.
- Run locally with `py manage.py runserver`; the sqlite DB (`db.sqlite3`) is versioned for demo data, so wipe/reset before shipping changes.
- Tests are currently placeholders; when you add behavior, create app-specific tests under `<app>/tests.py` and run them with `py manage.py test <app>` so CI commands stay consistent.

## Gotchas
- `ALLOWED_HOSTS` is limited to localhost; set env overrides before deploying anywhere else.
- Long-running queries can lock sqlite; existing config raises timeout at 20 s—prefer bulk updates over per-row saves inside loops.
- Remember to register new models/admin customizations in `blood/admin.py` or the relevant app admin modules so the built-in admin remains usable.
