"""
OEP-66 queryset-scoping building blocks for DRF list endpoints.

OEP-66 ("Separating Authorization Concerns in List Endpoints",
openedx-proposals#802) prescribes keeping three authorization concerns
separate on a list endpoint, each handled by a dedicated layer:

* **Endpoint access** — DRF ``permission_classes`` (may this user call the
  endpoint at all?). Returns ``403`` when denied.
* **Record visibility (queryset scoping)** — a :class:`ScopingPolicy` applied
  in ``get_queryset()`` by :class:`ScopedQuerysetMixin` (which rows may this
  user see?). Rows the user may not see are simply absent from the response;
  their absence is never a ``403``.
* **User-driven filtering** — a ``django-filter`` ``FilterSet`` (of the visible
  rows, which did the user ask for?). Narrows an already-authorized queryset
  and must never widen it.

This is *application-level queryset scoping*, not database-enforced
`row-level security`_: it only constrains queries that go through the view's
scoped queryset. Code that queries the model directly is not protected by it.

A policy resolves the subject's accessible scopes in **one bulk lookup**
(see openedx-authz ``get_scopes_for_subject_and_permission`` /
``get_scopes_for_user_and_permission``) and turns that scope set into a
``WHERE`` clause, rather than fetching every row and running an ``enforce``-style
check per object.

.. _row-level security: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
"""

from abc import ABC, abstractmethod

from django.core.exceptions import ImproperlyConfigured


class ScopingPolicy(ABC):
    """
    Scopes a queryset to the rows a user is permitted to see (OEP-66).

    A policy must not re-implement access rules; it delegates to the platform's
    authorization engine (e.g. openedx-authz) to resolve the subject's
    accessible scopes and translates that answer into a queryset filter. It is
    kept separate from the view so the same visibility rule can be reused and
    unit-tested independently of any endpoint.
    """

    @abstractmethod
    def scope(self, queryset, user):
        """Return ``queryset`` filtered to the rows visible to ``user``."""


class ScopedQuerysetMixin:
    """
    Applies :attr:`scoping_policy` to the base queryset of a DRF view (OEP-66).

    Mix into a ``GenericAPIView`` / ``ListAPIView`` (or a ``GenericViewSet``)
    and set :attr:`scoping_policy` to a :class:`ScopingPolicy` instance. The
    mixin runs the policy on top of the view's ``get_queryset()`` result so the
    ``list`` response only contains rows within the user's accessible scopes.
    """

    scoping_policy = None  # a ScopingPolicy instance

    def get_queryset(self):
        """Return the base queryset scoped to the rows the request user may see."""
        queryset = super().get_queryset()
        if self.scoping_policy is None:
            raise ImproperlyConfigured(
                f"{type(self).__name__} uses ScopedQuerysetMixin but does not set a scoping_policy"
            )
        return self.scoping_policy.scope(queryset, self.request.user)
