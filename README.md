# Blood Bank Management System
![developer](https://img.shields.io/badge/Developed%20By%20%3A-Sumit%20Kumar-red)
---
## Functions

### Admin
- Create Admin account using following command
```
py manage.py createsuperuser
```
- After Login, can see Unit of blood of each blood group available, Number Of Donor, Number of blood request, Number of approved request, Total Unit of blood on Dashboard.
- Can View, Update, Delete Donor.
- Can View, Update, Delete Patient.
- Can View Donation Request made by donor and can approve or reject that request based on disease of donor.
- If Donation Request approved by admin then that unit of blood added to blood stock of that blood group.
- If Donation Request rejected by admin then 0 unit of blood added to stock.
- Can View Blood Request made by donor / patient and can approve or reject that request.
- If Blood Request approved by admin then that unit of blood reduced from blood stock of that blood group.
- If Blood Request rejected by admin then 0 unit of blood reduced from stock.
- Can see history of blood request.
- Can Update Unit Of Particular Blood Group.
- Can verify donor coordinates and view all pin-ready donors on the interactive **Donor Map** (Leaflet + OSM) to validate coverage before approving requests.


### Donor
- Donor can create account by providing basic details.
- After Login, Donor can donate blood, After approval from admin only, blood will be added to blood stock.
- Donor can see their donation history with status (Pending, Approved, Rejected).
- Donor can also request for blood from blood stock.
- Donor can see their blood request history with status.
- Donor can see number of blood request Made, Approved, Pending, Rejected by Admin on their dashboard.
> **_NOTE:_**  Donor can donate blood and can also request for blood.





### Patient
- Create account (No Approval Required By Admin, Can Login After Signup)
- After Login, Can see number of blood request Made, Approved, Pending, Rejected by Admin on their dashboard.
- Patient can request for blood of specific blood group and unit from blood stock.
- Patient can see their blood request history with status (Pending, Approved, Rejected).

---

## HOW TO RUN THIS PROJECT
- Install Python(3.7.6) (Dont Forget to Tick Add to Path while installing Python)
- Download This Project Zip Folder and Extract it
- Move to project folder in Terminal. Then run following Commands :

```
python -m pip install -r requirements.txt
```

```
py manage.py makemigrations
py manage.py migrate
py manage.py runserver
```
- Now enter following URL in Your Browser Installed On Your Pc
```
http://127.0.0.1:8000/
```

### Generate Demo Data (Optional)
Need a populated dashboard for demos or screenshots? Run the management command below after migrating:

```
py manage.py seed_demo_data --purge --seed=123
```

- `--purge` wipes previously generated donors/patients/requests/donations before reseeding.
- `--seed` makes the output deterministic; omit it for fresh randomness.
- Use `--donors` or `--patients` to override the default 75‑100 range.

All generated donor/patient accounts share the password **DemoPass123!** so you can log in quickly.


### Built-in FAQ Copilot
- Visit `http://127.0.0.1:8000/assistant/` (or click **FAQ Assistant** in any navigation bar) to open the guided chatbot.
- The assistant ships with curated answers for admins, donors, patients, and anonymous visitors and mirrors the rules in `blood/views.py`.
- Quick prompt chips, live stock/request stats, and links to dashboards make it easy for new teammates to self-serve without reading the entire codebase.

### Urgent SMS Alerts (AWS SNS)
- Urgent blood requests now trigger SMS alerts to available donors via **Amazon SNS**. Enable the feature by exporting the variables below (or adding them to `.env`) before starting Django:

```
AWS_SNS_ENABLED=true
AWS_SNS_REGION=ap-south-1
AWS_SNS_DEFAULT_COUNTRY_CODE=+91
AWS_SNS_SENDER_ID=BLDBRDG
AWS_SNS_MAX_RECIPIENTS=0
AWS_SNS_MIN_NOTIFICATION_GAP_SECONDS=1800
```

- `AWS_SNS_MAX_RECIPIENTS=0` means unlimited recipients (use a positive number to cap alerts per urgent request).

### Production Safety Guard

- The app now supports explicit environment mode via `ENVIRONMENT`.
- Set `ENVIRONMENT=production` in real deployments. In this mode the app fails fast if security-critical settings are unsafe.

Required production variables:

```
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=<strong-random-secret>
ALLOWED_HOSTS=<your-domain-or-ip>
```

When `ENVIRONMENT=production`, startup will reject insecure defaults (for example Django auto `SECRET_KEY` or `DEBUG=True`).

- Configure AWS credentials with `aws configure` (or environment variables) on the same host running Django so boto3 can publish SMS messages.
- Donors must have valid E.164 phone numbers on their profiles; the helper falls back to the default country code for 10-digit numbers but storing `+<country><number>` avoids any ambiguity.
- When an urgent request is submitted (quick form, donor, or patient portals) the platform fetches available donors with matching blood groups, prioritizes those in the same zip/postal code, and texts up to `AWS_SNS_MAX_RECIPIENTS` recipients. Donor `last_notified_at` timestamps ensure no one is spammed more often than the configured gap.
- Missing dependencies or SNS failures are logged server-side so requests still succeed even if alerts cannot be delivered.

### SMS Health Check (Recommended)

Before demos/deployments, verify credentials + SNS permissions:

```
py manage.py sms_health_check
```

To send one live probe SMS after health passes:

```
py manage.py sms_health_check --to 9361046558 --probe
```

If you use temporary AWS credentials, ensure `AWS_SESSION_TOKEN` is set along with key + secret.

#### Start Ngrok Automatically
Skip the manual Ngrok download step by using the built-in helper that relies on [pyngrok](https://github.com/alexdlaird/pyngrok):

```
py manage.py start_ngrok
```

- The command opens a tunnel for `http://127.0.0.1:8000`, prints the HTTPS forwarding URL, and keeps the session alive until you press **Ctrl+C**.
- Pass `--authtoken <token>` once (or set `NGROK_AUTHTOKEN` in `.env`) so Ngrok can open longer sessions. Other useful flags:
	- `--port 9000` — expose a different local port.
	- `--no-inspect` — disable the Ngrok inspector UI if you don’t need request replay.
- The previous `--write-env` / `MATCHING_CONFIRMATION_BASE_URL` helpers were removed along with messaging, so the command now focuses solely on opening tunnels.

### AWS EC2 deployment (preserve current DB/media/logins/logs)

- Full guide: `DEPLOY_AWS_EC2_SQLITE.md`
- Prepare local state before sync:

```
powershell -ExecutionPolicy Bypass -File scripts/aws/prepare_local_state.ps1
```

- Sync your current DB/media/logs to EC2:

```
powershell -ExecutionPolicy Bypass -File scripts/aws/sync_state_to_ec2.ps1 -Ec2Host <EC2_PUBLIC_IP> -KeyPath "C:\path\to\key.pem"
```

- Start on EC2:

```
docker compose -f docker-compose.aws.sqlite.yml up -d --build
```


### Donor Geolocation & Map Verification
- Both the donor signup form and the admin donor edit screen now accept optional latitude/longitude pairs (decimal degrees). Enter both values to place a marker; leave both blank to skip mapping.
- Admins can open **Donor Map** from the sidebar to inspect pin-ready donors, toggle their "map verified" state, and focus only on donors with real coordinates. The page now embeds a Python-powered **Folium** globe so you can preview donor coverage server-side (ideal for screenshots or export) alongside the client-side controls.
- Blue markers represent verified donors; yellow markers are awaiting verification. Use the panel on the right to approve/reset verification and keep the map trustworthy.
- If a donor only provides an address, the platform now auto-geocodes it (OpenStreetMap / Nominatim + deterministic fixtures for common demo addresses) so their profile still lands on the map.
- Need to backfill historic donors? Click **Auto-place missing donors** on the Donor Map to process up to 25 addresses per batch, or run the management command below for a full sweep:

```
py manage.py geocode_donors --limit 200
```

Add `--dry-run` to preview updates without saving or `--force` to reprocess donors that already have coordinates.

- Demo data uses synthetic street names that real geocoders cannot resolve. Pass `--fallback` to auto-generate deterministic coordinates only when a lookup fails, or `--synthetic-only` to skip network calls entirely. The synthetic generator now snaps every address onto curated "+land only" tiles that span multiple continents, so no donor will sit in the ocean:

```
py manage.py geocode_donors --force --synthetic-only
```

The synthetic coordinates are stable per address (and still deterministic), so your map pins remain consistent between deployments even without internet access or remote geocoders.

- Already have bad historical pins floating in the sea? Run the land-locker to analyze every donor and relocate only the ocean entries:

```
py manage.py fix_ocean_donors --synthetic-only
```

Add `--dry-run` to preview the moves without writing changes, `--limit` to operate on a subset, or pass `--country=us` / similar to bias the optional remote geocoder before falling back to deterministic land tiles.

> **Heads-up:** The Folium renderer ships as an optional dependency. If the server console reports `folium` missing, install it with `python -m pip install folium` (already listed in `requirements.txt`). The donor map template will show a graceful error reminder until it becomes available.



