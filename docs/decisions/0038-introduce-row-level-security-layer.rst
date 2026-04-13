Introduce a Row-Level Security Layer
====================================

:Status: Proposed
:Date: 2026-04-14
:Deciders: API Working Group
:Technical Story: Open edX REST API Standards – Row-level security for list endpoints

Context
-------

List endpoints must return only items a user is allowed to see. Today, many APIs rely on ad-hoc
``has_access`` checks inside views, mixing three distinct concerns — endpoint access control,
record visibility filtering, and user-driven filtering — into a single block of view logic. This
makes behavior inconsistent across APIs, harder to audit, and more difficult to test.

The platform already contains ~150 call sites for ``has_access``
(``lms/djangoapps/courseware/access.py``) and several similar helpers such as
``has_studio_read_access()``, ``has_course_author_access()``, and ``get_course_with_access()``.
These are applied inline in views rather than through a reusable, composable layer.

Relevance in edx-platform
--------------------------

Current patterns that should be refactored:

* **CMS contentstore views** (``cms/djangoapps/contentstore/views/course.py``) call
  ``has_studio_read_access()`` and ``has_course_author_access()`` inline to decide which courses a
  user can see or modify, mixing endpoint-level guards with per-record visibility.
* **Enrollment API** (``openedx/core/djangoapps/enrollments/views.py``) performs inline
  ``user_has_role`` checks against ``CourseStaffRole`` and ``GlobalStaff`` rather than separating
  permission classes from queryset scoping.
* **Discussion REST API** (``lms/djangoapps/discussion/rest_api/views.py``) embeds per-record
  access decisions directly in view logic alongside user-driven filters.
* **Course Blocks API** (``lms/djangoapps/course_api/blocks/views.py``) imports
  ``lms.djangoapps.courseware.access.has_access`` to filter blocks by user after retrieval rather
  than scoping the data source upfront.

Relationship to ADR-0003 (Bridgekeeper)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An existing ADR (``lms/djangoapps/courseware/docs/decisions/0003-permissions-via-bridgekeeper.rst``)
proposes converting ``has_access`` calls to ``bridgekeeper``-based named permissions with queryset
filtering support. The current ADR is **complementary, not competing**: ADR-0003 addresses the
*permission engine* (how individual access checks are expressed and evaluated), while this ADR
defines the *architectural pattern* that views must follow (how endpoint access, record visibility,
and user-driven filtering are separated into distinct layers).

If bridgekeeper is adopted, its queryset-capable rules can serve as the implementation behind the
RLS scoping layer described here. If bridgekeeper is not adopted or is replaced by another
mechanism, the three-layer separation of concerns still applies. This ADR is intentionally
engine-agnostic.

Decision
--------

1. Define a reusable **Row-Level Security (RLS)** layer that governs record visibility by user and
   role.
2. Separate three concerns in every list endpoint:

   * **Endpoint access** — DRF permission classes (``permission_classes``). Answers: "Can this
     user call this endpoint at all?"
   * **Record visibility** — RLS policy applied in ``get_queryset()``. Answers: "Which rows is
     this user allowed to see?"
   * **User-driven filtering** — ``django-filter`` FilterSets or DRF filter backends. Answers:
     "Which subset of visible rows did the user request?"

3. Require that list endpoints use RLS-scoped querysets by default.

Code example (target view structure)
-------------------------------------

**Separation of concerns with a Django ORM-backed endpoint:**

.. code-block:: python

   # permissions.py — Endpoint access: DRF permission class
   from rest_framework.permissions import BasePermission, IsAuthenticated
   from common.djangoapps.student.auth import has_studio_read_access

   class HasCourseStaffAccess(BasePermission):
       """Checks that the user has staff-level read access to the course."""

       def has_permission(self, request, view):
           course_key = view.kwargs.get("course_key")
           if course_key is None:
               return False
           return has_studio_read_access(request.user, course_key)

.. code-block:: python

   # views.py — Record visibility via RLS-scoped queryset + user-driven filtering
   from django_filters.rest_framework import DjangoFilterBackend
   from rest_framework import viewsets
   from rest_framework.permissions import IsAuthenticated

   from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
   from .permissions import HasCourseStaffAccess
   from .serializers import CourseOverviewSerializer

   class CourseOverviewViewSet(viewsets.ReadOnlyModelViewSet):
       """
       List and retrieve course overviews visible to the requesting user.
       """
       serializer_class = CourseOverviewSerializer
       permission_classes = [IsAuthenticated, HasCourseStaffAccess]
       filter_backends = [DjangoFilterBackend]
       filterset_fields = ["org"]

       def get_queryset(self):
           # RLS layer: scope the queryset to rows this user may see.
           qs = CourseOverview.objects.all()
           return rls_scope_courses(qs, self.request.user)

**Reusable RLS mixin (target utility):**

.. code-block:: python

   class RLSQuerysetMixin:
       """
       Mixin that delegates queryset scoping to a pluggable ``rls_policy``.

       Subclasses set ``rls_policy`` to an object that implements
       ``scope(queryset, user) -> queryset``.
       """
       rls_policy = None  # Must be set by the concrete view

       def get_queryset(self):
           qs = super().get_queryset()
           if self.rls_policy is None:
               raise ImproperlyConfigured(
                   f"{self.__class__.__name__} must define rls_policy."
               )
           return self.rls_policy.scope(qs, self.request.user)

Consequences
------------

Positive
~~~~~~~~

* Clear, auditable security model: each concern is testable in isolation.
* Consistent results across APIs — every list endpoint follows the same pattern.
* Performance improvement for list views: queryset-level scoping avoids the current
  pattern of loading all records, then filtering in Python.
* Easier onboarding for new contributors who can follow a single, documented pattern.

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* Requires shared abstractions (mixin, policy interface) to be built and maintained.
* Migration work for existing list endpoints that interleave access checks with view logic.
* Not all data sources are backed by the Django ORM (e.g., modulestore XBlocks). For non-ORM
  sources, the RLS policy degrades to an in-memory filter rather than a queryset annotation, but
  the architectural separation still applies.
* ``django-filter`` is already a transitive dependency of edx-platform; this ADR promotes it to a
  first-class, required pattern for user-driven filtering, which increases coupling to that
  library.

Alternatives Considered
-----------------------

* **Keep ad-hoc ``has_access`` checks inside views**: rejected because the current pattern mixes
  three distinct concerns, produces inconsistent behavior across APIs, and is difficult to audit
  or test.
* **Rely solely on bridgekeeper (ADR-0003)**: bridgekeeper provides a permission engine with
  queryset support but does not prescribe how views should be structured. Adopting bridgekeeper
  alone would not guarantee that every list endpoint cleanly separates endpoint access, record
  visibility, and user-driven filtering. This ADR layers an architectural pattern on top of
  whatever permission engine is in use.
* **Use Django-Guardian for object-level permissions**: rejected because Django-Guardian requires
  per-object permission rows in the database, which does not scale well for the volume of
  course content in a typical Open edX installation. RLS queryset scoping derives visibility
  from existing role and enrollment data without additional storage.

Rollout Plan
------------

1. Build shared ``RLSQuerysetMixin`` and define the ``RLSPolicy`` interface (``scope(qs, user)``).
2. Audit existing list endpoints to catalogue how each one currently handles access and filtering.
3. Migrate high-impact, ORM-backed endpoints first
4. For modulestore-backed endpoints (e.g., Course Blocks), apply the same architectural
   separation using in-memory filtering behind the same policy interface.
5. Add linting or CI checks to enforce that new list endpoints include an ``rls_policy`` or
   equivalent scoping in ``get_queryset()``.
6. Update tests to validate that each endpoint returns only the records the requesting user
   should see.
7. Update API documentation to reflect the standardized pattern.

References
----------

* Existing ADR: ``lms/djangoapps/courseware/docs/decisions/0003-permissions-via-bridgekeeper.rst``
  — permission engine migration from ``has_access`` to named permissions.
* "Homogenize row level security" recommendation in the Open edX REST API standardization notes.
* Django REST Framework filtering documentation:
  https://www.django-rest-framework.org/api-guide/filtering/
* ``django-filter`` DRF integration:
  https://django-filter.readthedocs.io/en/stable/guide/rest_framework.html