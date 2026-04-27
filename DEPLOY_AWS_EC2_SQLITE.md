# Deploy on AWS EC2 with current data, logs, and same login credentials

This setup preserves your **existing users/passwords**, **all current records**, and **existing logs** by deploying with persistent SQLite + mounted folders.

## What is preserved
- `data/db.sqlite3` (all auth users + app data)
- `media/` (uploaded profile images/files)
- `logs/` (Django + Gunicorn logs)

Because the exact DB file is copied, login credentials remain unchanged.

---

## 1) Launch EC2
Recommended minimum: Ubuntu 22.04, t3.small, 20+ GB EBS.

Open inbound security group ports:
- `22` (SSH) from your IP
- `80` (HTTP) from anywhere
- `443` (HTTPS) if using reverse proxy/TLS

---

## 2) Clone project on EC2
```bash
git clone <your-repo-url> ~/bloodbridge
cd ~/bloodbridge
```

---

## 3) Install Docker on EC2
```bash
bash scripts/aws/bootstrap_ec2_docker.sh
```
Then logout/login once to apply Docker group membership.

---

## 4) Prepare local state (on your Windows machine)
From project root:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/aws/prepare_local_state.ps1
```
This ensures `data/db.sqlite3` exists and creates `media/` + `logs/` folders.

---

## 5) Configure `.env` for production
Create/update `.env` in project root (local copy to be uploaded):

```env
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=<strong-random-secret>
ALLOWED_HOSTS=<EC2_PUBLIC_IP>,localhost,127.0.0.1

AWS_SNS_ENABLED=true
AWS_SNS_REGION=ap-south-1
AWS_SNS_DEFAULT_COUNTRY_CODE=+91
AWS_SNS_MAX_RECIPIENTS=0
AWS_SNS_MIN_NOTIFICATION_GAP_SECONDS=1800
AWS_SNS_SMS_TYPE=Transactional

# Appointment slots
# If true, the first donor appointment visit will auto-create a few upcoming slots
# when the database has none (helps fresh deployments). If you want manual-only,
# set this to False and create slots from Admin > Appointments.
AUTO_SEED_APPOINTMENT_SLOTS=true
```

If using AWS IAM role on EC2, do not set static AWS keys in `.env`.

---

## 6) Sync current state to EC2
```powershell
powershell -ExecutionPolicy Bypass -File scripts/aws/sync_state_to_ec2.ps1 `
  -Ec2Host <EC2_PUBLIC_IP> `
  -KeyPath "C:\path\to\your-ec2-key.pem" `
  -User ubuntu `
  -RemoteDir "~/bloodbridge"
```

This uploads:
- `docker-compose.aws.sqlite.yml`
- `.env` (if present)
- `data/db.sqlite3`
- `media/`
- `logs/`

---

## 7) Start app on EC2
SSH into EC2:
```bash
cd ~/bloodbridge
mkdir -p data media logs
sudo docker compose -f docker-compose.aws.sqlite.yml up -d --build
sudo docker compose -f docker-compose.aws.sqlite.yml ps
```

Open:
- `http://<EC2_PUBLIC_IP>/`

---

## 8) Validate credentials/data/logs
```bash
# verify same users exist
sudo docker compose -f docker-compose.aws.sqlite.yml exec web python manage.py shell -c "from django.contrib.auth.models import User; print(User.objects.count())"

# verify data tables
sudo docker compose -f docker-compose.aws.sqlite.yml exec web python manage.py shell -c "from blood.models import BloodRequest; print(BloodRequest.objects.count())"

# verify persisted logs
ls -lah logs/
tail -n 100 logs/django.log || true
tail -n 100 logs/gunicorn_error.log || true
```

---

## Appointment slots (new)
- Admins can create/manage slots at: `http://<EC2_PUBLIC_IP>/admin-appointments/`
- Donors can request a slot at: `http://<EC2_PUBLIC_IP>/donor/appointments/`
- If you see “No active slots”, either:
  - Create slots from the admin page above, or
  - Ensure `AUTO_SEED_APPOINTMENT_SLOTS=true` is set (then open the donor appointment page once).

---

## 9) Backups (recommended)
On EC2, create timestamped backups:
```bash
cd ~/bloodbridge
mkdir -p backups
cp data/db.sqlite3 backups/db_$(date +%F_%H%M%S).sqlite3
```

For full state backup:
```bash
tar -czf backups/state_$(date +%F_%H%M%S).tar.gz data media logs .env
```

---

## Notes
- This is single-instance deployment optimized for preserving current state exactly.
- For horizontal scaling later, migrate to RDS PostgreSQL + S3 for media.
