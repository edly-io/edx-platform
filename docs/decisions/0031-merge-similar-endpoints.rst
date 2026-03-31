Merge Similar Endpoints
=======================

:Status: Proposed
:Date: 2026-03-31
:Deciders: Open edX Platform / API Working Group
:Technical Story: Open edX REST API Standards - Consolidation of fragmented same-resource endpoints into unified parameterised views

Context
-------

Open edX APIs currently expose multiple endpoints that perform closely related operations with only
minor variations in behaviour. Rather than consolidating these into a single parameterised resource,
the platform has grown a proliferation of narrow, action-scoped URLs — each duplicating validation,
permission-checking, and business logic from its siblings.

A prominent cluster illustrate the problem:

**Certificate endpoints** (``lms/djangoapps/instructor/views/api_urls.py``):

* ``enable_certificate_generation`` — enables or disables self-generated certificates for students
* ``start_certificate_generation`` — triggers bulk certificate generation for all enrolled students
* ``start_certificate_regeneration`` — regenerates certificates based on provided
  ``certificate_statuses``

All three are registered in ``api_urls.py`` as separate ``path()`` entries and each independently
validates ``course_id``, checks instructor permissions, and dispatches a background Celery task —
with near-identical boilerplate in each view.

The impact of this fragmentation is felt across several dimensions:

* **Redundant code**: Permission checks, serializer logic, and audit-logging are re-implemented
  independently across views, making fixes and feature additions error-prone.
* **Client complexity**: External systems and AI agents must discover, call, and handle errors for
  multiple endpoints to complete a single logical workflow.
* **Inconsistent contracts**: Divergent request/response shapes between sibling endpoints create
  subtle integration bugs and complicate contract testing.

Decision
--------

We will consolidate groups of closely related endpoints into **single, parameterised DRF views**
(or shared service layers), using an ``action`` (or equivalent) request parameter to distinguish
the operation being performed.

Implementation requirements:

* Identify endpoint groups that share the same resource domain and differ only in the operation
  applied to that resource.
* Expose a single URL per resource group accepting an ``action`` or ``mode`` field (or using HTTP
  verbs semantically where REST conventions apply cleanly).
* Move shared logic — permission checking, input validation, audit logging — into a common service
  layer or mixin that all operations invoke.
* Preserve backward compatibility via URL aliases or deprecation redirects for a defined transition
  window.
* Document the unified endpoint schema in drf-spectacular / OpenAPI, including the enumerated set
  of valid ``action`` / ``mode`` values and their respective request/response shapes.

Relevance in edx-platform
--------------------------

Confirmed fragmentation in the codebase:

* **Certificate views** (``lms/djangoapps/instructor/views/api_urls.py``, lines confirmed in
  master): The following three entries exist as separate ``path()`` registrations::

      path('enable_certificate_generation', api.enable_certificate_generation,
           name='enable_certificate_generation'),
      path('start_certificate_generation', api.StartCertificateGeneration.as_view(),
           name='start_certificate_generation'),
      path('start_certificate_regeneration', api.StartCertificateRegeneration.as_view(),
           name='start_certificate_regeneration'),

Code example (target unified endpoint)
---------------------------------------

**Proposed unified certificate task endpoint**:

.. code-block:: http

   POST /api/instructor/v1/certificate_task/{course_id}
   Content-Type: application/json

   {
     "mode": "generate"
   }

Valid ``mode`` values: ``generate``, ``regenerate``, ``toggle``.

**Example DRF view skeleton:**

.. code-block:: python

   # lms/djangoapps/instructor/views/api.py
   class CertificateTaskView(APIView):
       """Unified entry point for certificate generation lifecycle operations."""

       VALID_MODES = {"generate", "regenerate", "toggle"}

       def post(self, request, course_id):
           course_key = CourseKey.from_string(course_id)
           _check_instructor_permissions(request.user, course_key)

           mode = request.data.get("mode")
           if mode not in self.VALID_MODES:
               raise ValidationError({"mode": f"Must be one of: {self.VALID_MODES}"})

           service = CertificateTaskService(course_key)
           result = getattr(service, mode)(request.data)
           return Response(result, status=status.HTTP_200_OK)

Consequences
------------

Positive
~~~~~~~~

* Clients implement a single integration point per resource domain, reducing onboarding friction
  for external systems and AI agents.
* Shared validation, permission, and audit logic lives in one place, eliminating divergence between
  sibling endpoints.
* OpenAPI schemas become more compact — a single operation object per resource instead of three
  or more.
* Contract tests cover one endpoint per resource group, cutting test surface area without reducing
  coverage.
* The certificate consolidation aligns with an already-open upstream issue (#36961), increasing
  likelihood of community acceptance.

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* Existing clients calling the legacy URLs require a migration period; deprecated aliases must be
  maintained until adoption drops sufficiently.
* The ``mode`` / ``action`` parameter pattern diverges from strict REST conventions; teams must
  agree on a consistent naming standard across endpoint groups.
* A poorly designed service layer could become a "god object"; care must be taken to keep each
  operation handler cohesive and independently testable.

Alternatives Considered
-----------------------

* **Keep per-action endpoints**: Rejected. The duplication cost compounds with every new operation
  and makes consistent error handling and logging practically impossible to enforce.
* **Use HTTP verbs exclusively (pure REST)**: Partially applicable — ``POST`` for create,
  ``DELETE`` for unenroll — but breaks down for operations that do not map cleanly to HTTP verbs
  (e.g., ``enable_certificate_generation``). A hybrid approach (HTTP verbs where natural,
  ``action`` / ``mode`` parameter otherwise) is acceptable.
* **GraphQL mutations**: Considered but out of scope for this iteration; the platform's existing
  REST ecosystem makes a full GraphQL migration impractical in the near term.

Rollout Plan
------------

1. Implement the unified ``CertificateTaskView``; register
   legacy paths as deprecated aliases emitting a ``Deprecation`` response header.
2. Identify and document additional endpoint groups sharing a resource domain. Add them to the
   placeholder table below.
3. Announce a deprecation timeline to known API consumers and update developer documentation.
4. Remove legacy aliases after the deprecation window closes (target: two named Open edX releases).

References
----------

* Django REST Framework – Class-Based Views:
  https://www.django-rest-framework.org/api-guide/views/
