ADR-012: Document & Consolidate Internal APIs Used by MFEs
==========================================================

:Status: Proposed
:Date: 2026-04-09
:Deciders: API Working Group
:Technical Story: Open edX REST API Standards - MFE API documentation and consolidation

Context
-------

Multiple Open edX MFEs depend on undocumented internal LMS APIs. This causes two concrete problems:

1. **Runtime breakages**: Backend refactors silently break MFE data fetches because there
   is no contract guaranteeing response shape or field presence. MFE teams discover
   breakages only after deployment, since no schema validation exists to catch
   incompatible changes earlier in the development cycle.
2. **Blocked integrators**: External developers and AI-driven tooling cannot discover or
   rely on MFE-facing endpoints because they lack OpenAPI schemas, versioning, and
   deprecation guarantees. Without formal contracts, third-party systems have no stable
   surface to build against.

Decision
--------

We will document and consolidate all internal APIs used by MFEs into stable,
OpenAPI-described contracts.

Implementation requirements:

* All backend APIs consumed by MFEs MUST be documented with OpenAPI specifications,
  including field descriptions, types, and example responses.
* Consolidate MFE configuration into a single, documented endpoint per MFE context
  (target pattern: ``/api/mfe_config/v1/``).
* The consolidated endpoint MUST accept ``mfe=<name>`` for app-specific overrides and
  MAY accept ``course_id`` for course-contextual data (see `Authentication & Authorization`_
  below for scope rules).
* Deprecate undocumented internal endpoints once a supported, documented replacement
  exists. Deprecations MUST follow the `Open edX DEPR process (OEP-21)
  <https://open-edx-proposals.readthedocs.io/en/latest/processes/oep-0021-proc-deprecation.html>`_.
* Maintain backward compatibility during migration. If a breaking change is unavoidable,
  it MUST be handled by creating a new API version and transitioning consumers using the DEPR process.
* Use URL-path versioning (``/api/mfe_config/v1/``, ``/v2/``, etc.) consistent with
  existing Open edX API conventions.

Authentication & Authorization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The existing ``/api/mfe_config/v1`` endpoint does not require authentication. Adding
user-role context changes this boundary. The consolidated endpoint serves two categories
of data with different security profiles:

* **Public configuration** (``BASE_URL``, ``LMS_BASE_URL``, feature flags): No
  authentication required. Highly cacheable.
* **User-contextual data** (user roles, course-specific permissions): Requires a valid
  session or JWT. Responses MUST NOT be cached in shared caches.

When ``course_id`` is provided and the request is authenticated, the response MAY include
a ``user_context`` key containing role information. Unauthenticated requests with
``course_id`` return only public course metadata.

Relevance in edx-platform
--------------------------

Current patterns that should be migrated:

* **Existing MFE config API**: ``lms/djangoapps/mfe_config_api/views.py`` implements
  ``MFEConfigView`` at ``/api/mfe_config/v1`` (see ``lms/urls.py``). It returns merged
  config from legacy settings, ``MFE_CONFIG``, and ``MFE_CONFIG_OVERRIDES``, with optional
  ``?mfe=<name>`` for app-specific overrides.
* **Response shape**: JSON with keys such as ``BASE_URL``, ``LMS_BASE_URL``,
  ``STUDIO_BASE_URL``, ``ENABLE_COURSE_SORTING_BY_START_DATE``, etc.
* **Enrollment API**: Several MFEs call enrollment endpoints that return hand-built JSON
  without serializer validation.
* **Course metadata endpoints**: MFEs fetch course details from internal views that lack
  versioning or schema documentation.

Code examples
-------------

**Current pattern (edx-platform) — manual config merging, no schema:**

.. code-block:: python

   # lms/djangoapps/mfe_config_api/views.py  (simplified)
   class MFEConfigView(APIView):
       def get(self, request):
           legacy_config = self._get_legacy_config()
           mfe_config = configuration_helpers.get_value("MFE_CONFIG", settings.MFE_CONFIG)

           mfe_config_overrides = {}
           if request.query_params.get("mfe"):
               app_config = configuration_helpers.get_value(
                   "MFE_CONFIG_OVERRIDES", settings.MFE_CONFIG_OVERRIDES
               )
               mfe_config_overrides = app_config.get(request.query_params["mfe"], {})

           merged_config = legacy_config | mfe_config | mfe_config_overrides
           return JsonResponse(merged_config, status=200)

**Target pattern — serializer-backed, schema-documented, with optional user context:**

.. code-block:: python

   # serializers.py
   from rest_framework import serializers

   class MFEConfigSerializer(serializers.Serializer):
       BASE_URL = serializers.URLField(help_text="Root URL of the MFE deployment")
       LMS_BASE_URL = serializers.URLField(help_text="LMS base URL")
       STUDIO_BASE_URL = serializers.URLField(help_text="Studio base URL")
       # ... additional config fields documented explicitly

   class UserContextSerializer(serializers.Serializer):
       course_id = serializers.CharField(help_text="Course identifier")
       user_roles = serializers.ListField(
           child=serializers.CharField(),
           help_text="Roles the requesting user holds in this course context",
       )

   class MFEConfigWithContextSerializer(serializers.Serializer):
       config = MFEConfigSerializer(help_text="MFE configuration values")
       user_context = UserContextSerializer(
           required=False,
           help_text="Present only for authenticated requests with course_id",
       )

   # views.py
   from rest_framework.views import APIView
   from rest_framework.response import Response
   from rest_framework import status

   class MFEConfigView(APIView):
       """
       GET /api/mfe_config/v1/?mfe=learning
       GET /api/mfe_config/v1/?mfe=learning&course_id=course-v1:edX+DemoX+1T2024
       """
       def get(self, request):
           merged_config = self._build_merged_config(request.query_params.get("mfe"))

           payload = {"config": merged_config}

           course_id = request.query_params.get("course_id")
           if course_id and request.user.is_authenticated:
               payload["user_context"] = {
                   "course_id": course_id,
                   "user_roles": get_user_roles(request.user, course_id),
               }

           serializer = MFEConfigWithContextSerializer(payload)
           return Response(serializer.data, status=status.HTTP_200_OK)

Consequences
------------

Positive
~~~~~~~~

* Stabilizes MFE-backend contracts; backend refactors can be validated against the
  OpenAPI schema before deployment.
* Improves discoverability for external integrators and AI tooling through schema-based
  documentation.
* Enables automatic documentation generation and client SDK creation from OpenAPI specs.

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* Requires cataloging API calls across multiple MFE repositories and their backend
  dependencies — a non-trivial audit effort.
* MFE teams will need to update their data-fetching code to point to consolidated
  endpoints, requiring cross-team coordination.
* Maintaining both old and new endpoints during the compatibility window increases
  short-term maintenance burden.
* Existing MFE client code that expects legacy response shapes may need updates;
  these changes should be tracked per-MFE in migration tickets.

Alternatives Considered
-----------------------

* **Keep existing undocumented endpoints**: Rejected because fragile couplings continue
  to cause runtime breakages and block external integrators from building on MFE-facing
  APIs.
* **Document each MFE's APIs independently without consolidation**: Rejected because it
  perpetuates duplication across repositories and does not reduce the total number of
  internal endpoints that MFEs depend on.
* **Introduce a Backend-for-Frontend (BFF) layer**: Rejected for now because it adds a
  new service to deploy and maintain. The consolidated config endpoint achieves the
  primary goal (stable contracts) with less operational overhead. A BFF can be revisited
  if MFE data-fetching patterns grow significantly more complex.

Rollout Plan
------------

1. Audit all MFE repositories to catalog backend API dependencies. Track results in a
   shared spreadsheet or wiki page linked from this ADR.
2. Prioritize high-impact MFEs: Learning, ORA, Progress, Discussions.
3. Document the existing ``/api/mfe_config/v1`` contract in OpenAPI.
4. Create shared serializer utilities (e.g., ``MFEConfigSerializer``) in
   ``openedx/core/djangoapps/`` for reuse across endpoints.
5. Extend the endpoint with optional ``course_id`` and user-role context.
6. Provide a compatibility window (minimum 6 months / one named release, per OEP-21)
   where both old and new endpoints are available.
7. File DEPR tickets for each deprecated internal endpoint and track migration status
   per MFE in a dashboard or project board.
8. Deprecate ad-hoc internal endpoints once replacements are stable and all MFEs have
   migrated.

References
----------

* `OEP-21: Deprecation and Removal Process <https://open-edx-proposals.readthedocs.io/en/latest/processes/oep-0021-proc-deprecation.html>`_
* `OEP-65: Frontend Composability <https://docs.openedx.org/projects/openedx-proposals/en/latest/architectural-decisions/oep-0065-arch-frontend-composability.html>`_
  (related — covers MFE shared dependency architecture)
* "Undocumented Internal APIs Used by MFEs" recommendation in the Open edX REST API
  standardization notes.
