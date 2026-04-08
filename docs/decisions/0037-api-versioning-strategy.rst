Versioning Strategy — Non-Versioned as Default
==============================================

:Status: Proposed
:Date: 2026-04-08
:Deciders: API Working Group

Context
=======

Open edX has multiple API versions in parallel (e.g., v0/v1/v3) which creates confusion about which
version is stable or deprecated and increases the risk that external systems rely on outdated contracts.

Decision
========

1. Treat **non-versioned** endpoints as the default "current stable" API surface where feasible.
2. When a breaking change is required:

   * Create a new **versioned** API (e.g., ``/api/foo/v2/``).
   * Replace the non-versioned endpoint to point to the newly versioned implementation so that
     the default remains stable and predictable.

3. Publish explicit deprecation policies:

   * Mark old versions as deprecated in schema/docs.
   * Provide migration guides and a removal timeline.

Relevance in edx-platform
=========================

* **Current mix**: LMS and CMS use both versioned and non-versioned API paths.
  Examples: ``api/enrollment/v1/``, ``api/val/v0/``, ``api/instructor/v1/`` and
  ``v2/``, ``api/user/v1/`` and ``api/user/v2/``, ``api/mfe_config/v1``,
  ``api/course_experience/`` (no version in path), ``api/xblock/v2/``,
  ``api/libraries/v2/`` (see ``lms/urls.py``, ``openedx/core/djangoapps/user_authn/urls_common.py``).
* **Confusion**: Multiple versions (v0, v1, v2, v3) without a single “default”
  make it unclear which endpoint clients should use.

Code example (routing pattern)
=============================

**Default (non-versioned) as stable:**

.. code-block:: python

   # urls.py: default entry point
   urlpatterns = [
       path("api/courses/", include("course_api.urls")),  # current stable
       path("api/courses/v2/", include("course_api.v2.urls")),  # new version when breaking
   ]

**When introducing a breaking change:**

.. code-block:: text

   1. Add /api/courses/v2/ with new contract.
   2. Document v2 in OpenAPI; mark v1 (or old default) as deprecated with removal date.
   3. Point /api/courses/ to v2 implementation so default stays stable; or keep
      /api/courses/ as v1 until deprecation period ends.

Consequences
============

* Pros

  * Reduces ambiguity for clients and agents selecting an endpoint.
  * Keeps a single “default” entry point stable.

* Cons / Costs

  * Requires careful routing and doc updates to avoid breaking existing consumers.

Implementation Notes
====================

* Ensure OpenAPI clearly flags deprecated versions and identifies the default.
* Automate "latest" selection in SDK generation based on schema metadata.

References
==========

* “Versioning confusion / deprecated versions” recommendation in the Open edX REST API standardization notes.
