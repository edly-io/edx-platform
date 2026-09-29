"""
Serializers for the Uber Learn Progress API.
"""
from rest_framework import serializers

VALID_ASSESSMENT_TYPES = ['baseline', 'final', 'retention']


class ActivityRequestSerializer(serializers.Serializer):
    """Validates the POST /activity request body."""

    activity_key = serializers.CharField(max_length=512)
    correct = serializers.BooleanField(allow_null=True, required=False, default=None)


class AssessmentRequestSerializer(serializers.Serializer):
    """
    Validates the POST /assessment request body.

    The server is authoritative for scoring — no score or answers are
    accepted from the client in v1.
    """

    assessment_type = serializers.ChoiceField(choices=VALID_ASSESSMENT_TYPES)
    idempotency_key = serializers.UUIDField()
