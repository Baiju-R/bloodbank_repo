import random

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from faker import Faker

from blood.models import Feedback
from donor.models import Donor
from patient.models import Patient


DEFAULT_MESSAGES = [
    "The process was smooth and the staff was very supportive. Happy to help save lives!",
    "Quick approval and clear communication. BloodBridge made everything easy.",
    "Very professional experience — I felt safe and informed throughout.",
    "Great platform! The request workflow is simple and transparent.",
    "Excellent coordination and timely updates. Thank you for this initiative.",
    "The donation scheduling and follow-up were fantastic. Proud to be part of this community.",
    "My request was handled quickly. The notifications and status updates were helpful.",
    "Amazing service! I appreciate the respectful and efficient process.",
    "The whole experience was comforting. Strongly recommend using BloodBridge.",
    "Fast, clear, and trustworthy. It really connects people who need help.",
]

ADMIN_REPLIES = [
    "Thank you for sharing your experience — we appreciate you!",
    "We’re grateful for your support. Your feedback helps us improve.",
    "Thanks for being part of BloodBridge. Wishing you good health!",
    "We’re glad the process felt smooth. Thank you for trusting BloodBridge.",
]

ADMIN_REACTIONS = ["👍", "❤️", "🙏", "👏", "😊", "🎉"]


class Command(BaseCommand):
    help = "Seed demo public feedback items (default 15)."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=15, help="How many feedbacks to create (default 15)")
        parser.add_argument("--seed", type=int, default=123, help="Random seed for deterministic output")
        parser.add_argument("--force", action="store_true", help="Create even if demo feedback already exists")

    def handle(self, *args, **options):
        count = int(options["count"])
        Faker.seed(int(options["seed"]))
        random.seed(int(options["seed"]))
        faker = Faker()

        used_names = set()

        def unique_display_name() -> str:
            # Keep names short and realistic (and stable-ish for a given seed).
            for _ in range(40):
                name = f"{faker.first_name()} {faker.last_name()}".strip()
                name = name[:60].strip()
                if name and name.lower() not in used_names:
                    used_names.add(name.lower())
                    return name
            # Fallback if faker starts colliding.
            suffix = random.randint(1000, 9999)
            name = f"{faker.first_name()} {faker.last_name()} {suffix}"[:60].strip()
            used_names.add(name.lower())
            return name

        existing_demo = Feedback.objects.filter(is_seeded_demo=True).count()
        if existing_demo and not options.get("force"):
            self.stdout.write(self.style.WARNING("Demo feedback already exists. Use --force to add more."))
            return

        donors = list(Donor.objects.select_related("user").all()[:25])
        patients = list(Patient.objects.select_related("user").all()[:25])

        created = 0
        with transaction.atomic():
            for idx in range(1, count + 1):
                rating = random.choices([5, 4, 3], weights=[75, 22, 3])[0]
                feedback_for = random.choice([Feedback.FEEDBACK_DONATION, Feedback.FEEDBACK_REQUEST, Feedback.FEEDBACK_GENERAL])

                # Prefer attaching to real donors/patients if present; fallback to anonymous.
                attach_mode = random.choice(["donor", "patient", "anon"]) if (donors or patients) else "anon"

                fb = Feedback(
                    feedback_for=feedback_for,
                    rating=rating,
                    message=random.choice(DEFAULT_MESSAGES) + " " + faker.sentence(nb_words=12),
                    is_public=True,
                    is_seeded_demo=True,
                    created_at=timezone.now(),
                )

                if attach_mode == "donor" and donors:
                    fb.author_type = Feedback.AUTHOR_DONOR
                    fb.donor = random.choice(donors)
                    fb.display_name = ""
                elif attach_mode == "patient" and patients:
                    fb.author_type = Feedback.AUTHOR_PATIENT
                    fb.patient = random.choice(patients)
                    fb.display_name = ""
                else:
                    fb.author_type = Feedback.AUTHOR_ANONYMOUS
                    fb.display_name = unique_display_name()

                # Add admin reply/reaction to many of them for a nicer homepage.
                if random.random() < 0.75:
                    fb.admin_reaction = random.choice(ADMIN_REACTIONS)
                if random.random() < 0.65:
                    fb.admin_reply = random.choice(ADMIN_REPLIES)
                    fb.admin_updated_at = timezone.now()

                fb.save()
                created += 1

        self.stdout.write(self.style.SUCCESS(f"Created {created} feedback(s)."))
