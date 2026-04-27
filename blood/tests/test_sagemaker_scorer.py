"""
Tests for the SageMaker Donor Recommendation AI Scorer.

Covers:
  - Feature extraction correctness
  - Fallback scoring (local heuristic)
  - SageMaker endpoint invocation (mocked)
  - Error handling / graceful degradation
  - DonorRecommendation AI score integration
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from donor.models import Donor, BloodDonate, MedicalReport
from blood.models import BloodRequest, Stock
from blood.services.sagemaker_scorer import (
    FEATURE_NAMES,
    extract_features,
    score_donors,
    _fallback_score_single,
    _features_to_csv_row,
    SageMakerScore,
)
from blood.services.donor_recommender import recommend_donors_for_request


class FeatureExtractionTest(TestCase):
    """Test that donor features are correctly extracted."""

    def setUp(self):
        self.user = User.objects.create(username="feat_donor")
        self.donor = Donor.objects.create(
            user=self.user,
            bloodgroup="A+",
            address="123 Test St",
            mobile="+919385425650",
            zipcode="600001",
            is_available=True,
            sex="M",
            date_of_birth=date(1990, 5, 15),
            weight_kg=75,
            hemoglobin_g_dl=Decimal("14.5"),
            blood_pressure_systolic=120,
            blood_pressure_diastolic=80,
            has_chronic_disease=False,
            on_medication=False,
            smokes=False,
            latitude=Decimal("13.0827"),
            longitude=Decimal("80.2707"),
            last_donated_at=date.today() - timedelta(days=90),
        )
        BloodDonate.objects.create(
            donor=self.donor, age=34, bloodgroup="A+", unit=200, status="Approved",
        )

    def test_feature_count_matches_feature_names(self):
        features = extract_features(self.donor, "A+", "600001")
        self.assertEqual(len(features), len(FEATURE_NAMES))
        for name in FEATURE_NAMES:
            self.assertIn(name, features)

    def test_blood_match_feature(self):
        feats_match = extract_features(self.donor, "A+", "")
        self.assertEqual(feats_match["blood_match"], 1.0)

        feats_no_match = extract_features(self.donor, "O-", "")
        self.assertEqual(feats_no_match["blood_match"], 0.0)

    def test_zipcode_match(self):
        feats = extract_features(self.donor, "A+", "600001")
        self.assertEqual(feats["same_zipcode"], 1.0)

        feats_diff = extract_features(self.donor, "A+", "500001")
        self.assertEqual(feats_diff["same_zipcode"], 0.0)

    def test_availability_feature(self):
        feats = extract_features(self.donor, "A+", "")
        self.assertEqual(feats["is_available"], 1.0)

        self.donor.is_available = False
        self.donor.save()
        feats = extract_features(self.donor, "A+", "")
        self.assertEqual(feats["is_available"], 0.0)

    def test_medical_features_populated(self):
        feats = extract_features(self.donor, "A+", "")
        self.assertGreater(feats["age_years"], 0)
        self.assertEqual(feats["weight_kg"], 75.0)
        self.assertEqual(feats["hemoglobin_g_dl"], 14.5)
        self.assertEqual(feats["bp_systolic"], 120.0)
        self.assertEqual(feats["bp_diastolic"], 80.0)
        self.assertEqual(feats["sex_male"], 1.0)
        self.assertEqual(feats["sex_female"], 0.0)

    def test_risk_flags(self):
        feats = extract_features(self.donor, "A+", "")
        self.assertEqual(feats["has_chronic_disease"], 0.0)
        self.assertEqual(feats["on_medication"], 0.0)
        self.assertEqual(feats["smokes"], 0.0)

        self.donor.has_chronic_disease = True
        self.donor.on_medication = True
        self.donor.smokes = True
        self.donor.save()
        feats = extract_features(self.donor, "A+", "")
        self.assertEqual(feats["has_chronic_disease"], 1.0)
        self.assertEqual(feats["on_medication"], 1.0)
        self.assertEqual(feats["smokes"], 1.0)

    def test_donation_count(self):
        feats = extract_features(self.donor, "A+", "")
        self.assertEqual(feats["donation_count"], 1.0)

    def test_recovery_detection(self):
        self.donor.last_donated_at = date.today() - timedelta(days=10)
        self.donor.save()
        feats = extract_features(self.donor, "A+", "")
        self.assertEqual(feats["in_recovery"], 1.0)

        self.donor.last_donated_at = date.today() - timedelta(days=120)
        self.donor.save()
        feats = extract_features(self.donor, "A+", "")
        self.assertEqual(feats["in_recovery"], 0.0)

    def test_missing_medical_fields(self):
        user2 = User.objects.create(username="sparse_donor")
        sparse_donor = Donor.objects.create(
            user=user2, bloodgroup="B+", address="sparse",
            mobile="+919999999999", is_available=True,
        )
        feats = extract_features(sparse_donor, "B+", "")
        self.assertEqual(feats["age_years"], -1.0)
        self.assertEqual(feats["weight_kg"], -1.0)
        self.assertEqual(feats["hemoglobin_g_dl"], -1.0)
        self.assertEqual(feats["bp_systolic"], -1.0)
        self.assertEqual(feats["bp_diastolic"], -1.0)
        self.assertLess(feats["profile_completeness"], 0.5)

    def test_profile_completeness(self):
        feats = extract_features(self.donor, "A+", "")
        self.assertGreater(feats["profile_completeness"], 0.8)


class CSVSerializationTest(TestCase):
    """Test CSV row generation for SageMaker payload."""

    def test_csv_row_has_correct_column_count(self):
        features = {name: float(i) for i, name in enumerate(FEATURE_NAMES)}
        row = _features_to_csv_row(features)
        values = row.split(",")
        self.assertEqual(len(values), len(FEATURE_NAMES))

    def test_csv_row_preserves_order(self):
        features = {name: float(i) for i, name in enumerate(FEATURE_NAMES)}
        row = _features_to_csv_row(features)
        values = [float(v) for v in row.split(",")]
        for i, name in enumerate(FEATURE_NAMES):
            self.assertEqual(values[i], float(i))


class FallbackScoringTest(TestCase):
    """Test the deterministic fallback scoring (no SageMaker)."""

    def test_perfect_donor_scores_high(self):
        features = {
            "blood_match": 1.0,
            "is_available": 1.0,
            "same_zipcode": 1.0,
            "has_coordinates": 1.0,
            "age_years": 30.0,
            "weight_kg": 75.0,
            "hemoglobin_g_dl": 14.5,
            "bp_systolic": 120.0,
            "bp_diastolic": 80.0,
            "has_chronic_disease": 0.0,
            "on_medication": 0.0,
            "smokes": 0.0,
            "sex_male": 1.0,
            "sex_female": 0.0,
            "days_since_donation": 120.0,
            "in_recovery": 0.0,
            "donation_count": 5.0,
            "profile_completeness": 1.0,
        }
        score = _fallback_score_single(features)
        self.assertGreaterEqual(score, 80.0)
        self.assertLessEqual(score, 100.0)

    def test_risky_donor_scores_low(self):
        features = {
            "blood_match": 0.0,
            "is_available": 0.0,
            "same_zipcode": 0.0,
            "has_coordinates": 0.0,
            "age_years": -1.0,
            "weight_kg": -1.0,
            "hemoglobin_g_dl": -1.0,
            "bp_systolic": -1.0,
            "bp_diastolic": -1.0,
            "has_chronic_disease": 1.0,
            "on_medication": 1.0,
            "smokes": 1.0,
            "sex_male": 0.0,
            "sex_female": 0.0,
            "days_since_donation": -1.0,
            "in_recovery": 0.0,
            "donation_count": 0.0,
            "profile_completeness": 0.0,
        }
        score = _fallback_score_single(features)
        self.assertLessEqual(score, 20.0)
        self.assertGreaterEqual(score, 0.0)

    def test_score_clamped_0_to_100(self):
        # Even extreme features should stay in range
        features = {name: 999.0 for name in FEATURE_NAMES}
        score = _fallback_score_single(features)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)


class ScoreDonorsTest(TestCase):
    """Test the score_donors() function with fallback and mocked SageMaker."""

    def setUp(self):
        self.user = User.objects.create(username="score_donor")
        self.donor = Donor.objects.create(
            user=self.user,
            bloodgroup="O+",
            address="Score St",
            mobile="+919385425650",
            is_available=True,
            weight_kg=70,
        )

    @override_settings(SAGEMAKER_ENDPOINT_ENABLED=False)
    def test_fallback_mode_returns_scores(self):
        results = score_donors([self.donor], "O+", "")
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], SageMakerScore)
        self.assertEqual(results[0].source, "fallback")
        self.assertGreaterEqual(results[0].ai_score, 0.0)
        self.assertLessEqual(results[0].ai_score, 100.0)

    @override_settings(SAGEMAKER_ENDPOINT_ENABLED=False)
    def test_empty_donors_returns_empty(self):
        results = score_donors([], "O+", "")
        self.assertEqual(results, [])

    @override_settings(SAGEMAKER_ENDPOINT_ENABLED=True)
    @patch("blood.services.sagemaker_scorer._invoke_endpoint")
    def test_sagemaker_mode_returns_scores(self, mock_invoke):
        mock_invoke.return_value = [0.85]  # 85% probability
        results = score_donors([self.donor], "O+", "")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source, "sagemaker")
        self.assertAlmostEqual(results[0].ai_score, 85.0, places=0)
        mock_invoke.assert_called_once()

    @override_settings(SAGEMAKER_ENDPOINT_ENABLED=True)
    @patch("blood.services.sagemaker_scorer._invoke_endpoint")
    def test_sagemaker_failure_falls_back(self, mock_invoke):
        mock_invoke.side_effect = ConnectionError("endpoint unreachable")
        results = score_donors([self.donor], "O+", "")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source, "fallback")

    @override_settings(SAGEMAKER_ENDPOINT_ENABLED=True)
    @patch("blood.services.sagemaker_scorer._invoke_endpoint")
    def test_sagemaker_wrong_count_falls_back(self, mock_invoke):
        mock_invoke.return_value = [0.5, 0.6]  # Wrong count for 1 donor
        results = score_donors([self.donor], "O+", "")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source, "fallback")


class RecommenderAIIntegrationTest(TestCase):
    """Test that recommend_donors_for_request populates ai_score."""

    def setUp(self):
        self.user = User.objects.create(username="ai_rec_donor")
        self.donor = Donor.objects.create(
            user=self.user,
            bloodgroup="AB+",
            address="AI St",
            mobile="+919385425650",
            is_available=True,
        )
        MedicalReport.objects.create(
            donor=self.donor,
            document=SimpleUploadedFile('report.pdf', b'%PDF-1.4 fake', content_type='application/pdf'),
            document_name='report.pdf',
        )
        self.req = BloodRequest.objects.create(
            patient=None, request_by_donor=None,
            patient_name="AI Patient", patient_age=30,
            reason="Test", bloodgroup="AB+", unit=200,
            status="Pending",
        )

    @override_settings(SAGEMAKER_ENDPOINT_ENABLED=False)
    def test_recommendations_have_ai_score(self):
        recs = recommend_donors_for_request(self.req, limit=10, require_eligible=True)
        self.assertGreaterEqual(len(recs), 1)
        rec = recs[0]
        # ai_score should be populated via fallback
        self.assertIsNotNone(rec.ai_score)
        self.assertGreaterEqual(rec.ai_score, 0.0)
        self.assertLessEqual(rec.ai_score, 100.0)
        self.assertIn(rec.ai_source, ("fallback", "sagemaker"))

    @override_settings(SAGEMAKER_ENDPOINT_ENABLED=True)
    @patch("blood.services.sagemaker_scorer._invoke_endpoint")
    def test_recommendations_with_sagemaker(self, mock_invoke):
        mock_invoke.return_value = [0.92]
        recs = recommend_donors_for_request(self.req, limit=10, require_eligible=True)
        self.assertGreaterEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec.ai_source, "sagemaker")
        self.assertAlmostEqual(rec.ai_score, 92.0, places=0)

    @override_settings(SAGEMAKER_ENDPOINT_ENABLED=True)
    @patch("blood.services.sagemaker_scorer._invoke_endpoint")
    def test_sagemaker_error_still_returns_recommendations(self, mock_invoke):
        """Even if SageMaker fails, rule-based recommendations still work."""
        mock_invoke.side_effect = Exception("boom")
        recs = recommend_donors_for_request(self.req, limit=10, require_eligible=True)
        self.assertGreaterEqual(len(recs), 1)
        # Fallback should kick in
        self.assertIn(recs[0].ai_source, ("fallback", "none"))
