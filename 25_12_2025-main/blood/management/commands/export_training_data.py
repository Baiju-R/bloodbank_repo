"""
Export donor feature data to CSV for SageMaker model training.

Generates a training dataset where each row contains:
  - Donor profile features (medical, location, availability)
  - A synthetic "success" label derived from historical donation outcomes

Usage:
    python manage.py export_training_data --output training_data.csv
    python manage.py export_training_data --output training_data.csv --include-labels
"""

import csv
import logging
import os

from django.core.management.base import BaseCommand
from django.utils import timezone

from donor.models import Donor, BloodDonate
from blood.models import BloodRequest, Stock
from blood.services.sagemaker_scorer import FEATURE_NAMES, extract_features

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Export donor features as CSV for SageMaker XGBoost training"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default="training_data.csv",
            help="Output CSV file path (default: training_data.csv)",
        )
        parser.add_argument(
            "--include-labels",
            action="store_true",
            help="Include a 'label' column (1=successful donor, 0=not) derived from donation history",
        )
        parser.add_argument(
            "--no-header",
            action="store_true",
            help="Omit the CSV header row (required for SageMaker XGBoost built-in)",
        )

    def handle(self, *args, **options):
        output_path = options["output"]
        include_labels = options["include_labels"]
        no_header = options["no_header"]

        donors = Donor.objects.select_related("user").all()
        blood_groups = list(Stock.objects.values_list("bloodgroup", flat=True))
        if not blood_groups:
            blood_groups = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]

        self.stdout.write(f"Exporting features for {donors.count()} donors × {len(blood_groups)} blood groups...")

        rows_written = 0

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            # Header
            if not no_header:
                header = list(FEATURE_NAMES)
                if include_labels:
                    header = ["label"] + header  # SageMaker XGBoost expects label first
                writer.writerow(header)

            for donor in donors:
                # Compute label: was this donor ever approved for a donation?
                approved_donations = BloodDonate.objects.filter(
                    donor=donor, status="Approved"
                ).count()
                label = 1 if approved_donations > 0 else 0

                # Generate one row per blood group the donor could potentially match
                for bg in blood_groups:
                    features = extract_features(donor, bg, donor.zipcode or "")
                    row_values = [features[name] for name in FEATURE_NAMES]

                    if include_labels:
                        # For XGBoost: label must be first column
                        row_values = [label] + row_values

                    writer.writerow(row_values)
                    rows_written += 1

        file_size = os.path.getsize(output_path)
        self.stdout.write(self.style.SUCCESS(
            f"Exported {rows_written} rows to {output_path} ({file_size:,} bytes)"
        ))

        if include_labels:
            self.stdout.write(
                f"\nTo train on SageMaker:\n"
                f"  1. Upload {output_path} to S3\n"
                f"  2. Use the XGBoost built-in algorithm with:\n"
                f"     - content_type = 'text/csv'\n"
                f"     - objective = 'binary:logistic'\n"
                f"     - num_round = 100\n"
                f"     - max_depth = 5\n"
                f"  3. Deploy the model as endpoint 'bloodbridge-donor-recommender'\n"
                f"  4. Set SAGEMAKER_ENDPOINT_ENABLED=true in your .env\n"
            )
