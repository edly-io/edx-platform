"""Tests for the OEP-66 queryset-scoping building blocks (``scoping.py``)."""
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, TestCase
from rest_framework.generics import GenericAPIView

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.lib.api.scoping import ScopedQuerysetMixin, ScopingPolicy


class _RecordingPolicy(ScopingPolicy):
    """Test policy that records its ``scope`` call and returns a sentinel."""

    def __init__(self, returns):
        self.returns = returns
        self.calls = []

    def scope(self, queryset, user):
        self.calls.append((queryset, user))
        return self.returns


class ScopingPolicyTests(TestCase):
    """``ScopingPolicy`` is an abstract base requiring ``scope``."""

    def test_cannot_instantiate_without_scope(self):
        with pytest.raises(TypeError):
            ScopingPolicy()  # pylint: disable=abstract-class-instantiated

    def test_subclass_implementing_scope_is_usable(self):
        policy = _RecordingPolicy(returns="scoped")
        assert policy.scope("base", user=None) == "scoped"


class ScopedQuerysetMixinTests(TestCase):
    """``ScopedQuerysetMixin.get_queryset`` applies the policy on top of ``super()``."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.request = RequestFactory().get("/")
        self.request.user = self.user

    def _build_view(self, policy):
        """Return a mixin-based view instance wired with ``policy`` and the test request."""
        test_request = self.request

        class _View(ScopedQuerysetMixin, GenericAPIView):
            scoping_policy = policy
            request = test_request

        return _View()

    def test_applies_policy_to_super_queryset(self):
        policy = _RecordingPolicy(returns="SCOPED")
        view = self._build_view(policy)
        with mock.patch.object(GenericAPIView, "get_queryset", return_value="BASE"):
            result = view.get_queryset()
        assert result == "SCOPED"
        # The policy was handed the base queryset and the request user.
        assert policy.calls == [("BASE", self.user)]

    def test_missing_policy_raises_improperly_configured(self):
        view = self._build_view(policy=None)
        with mock.patch.object(GenericAPIView, "get_queryset", return_value="BASE"):
            with pytest.raises(ImproperlyConfigured):
                view.get_queryset()
