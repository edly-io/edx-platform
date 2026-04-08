Standardize Pagination Across APIs
===================================

:Status: Proposed
:Date: 2026-04-08
:Deciders: API Working Group
:Technical Story: Open edX REST API Standards - Pagination standardization for consistency and scalability

Context
-------

Open edX platform API endpoints use multiple, inconsistent pagination strategies. Some endpoints use ``limit``/``offset`` query parameters, others use ``page``/``page_size``, and several return complete result sets with no pagination at all. This inconsistency forces every API consumer — whether a frontend micro-frontend (MFE), a mobile client, an AI agent, or a third-party integration — to implement custom data-loading logic per endpoint.

The ``edx-drf-extensions`` library already provides a ``DefaultPagination`` class (a subclass of DRF's ``PageNumberPagination``) that standardizes on ``page``/``page_size`` parameters with a default page size of 10 and a maximum of 100. However, many endpoints either override this with ad-hoc pagination classes, use ``LimitOffsetPagination``, or bypass pagination entirely by returning raw lists or manually constructed JSON arrays.

Decision
--------

We will standardize all Open edX REST APIs to use the existing ``DefaultPagination`` class from ``edx-drf-extensions`` as the platform-wide pagination standard.

Implementation requirements:

* All list-type API endpoints MUST use ``DefaultPagination`` (or a subclass of it) from ``edx-drf-extensions``.
* Endpoints currently using ``LimitOffsetPagination`` MUST be migrated to ``DefaultPagination`` with appropriate versioning.
* Endpoints returning unpaginated result sets MUST be updated to return paginated responses.
* All paginated responses MUST include the standard envelope: ``count``, ``next``, ``previous``, ``num_pages``, ``current_page``, ``start``, and ``results``.
* Views that subclass ``APIView`` directly (rather than ``GenericAPIView`` or ``ListAPIView``) MUST manually invoke the pagination API to return paginated responses.
* Custom ``page_size`` overrides per endpoint are acceptable when justified (e.g., mobile APIs may use a smaller default), but MUST be implemented by subclassing ``DefaultPagination`` rather than using an unrelated pagination class.
* Maintain backward compatibility for all APIs during migration. If a fully compatible migration is not possible, a new API version MUST be created and the old version deprecated following the standard deprecation process.

Relevance in edx-platform
--------------------------

Current example patterns that should be migrated:

* **Completion API** (``/api/completion/v1/completion/``) — uses inconsistent pagination formats depending on request parameters; some paths return unpaginated results.
* **User Accounts API** (``/api/user/v1/accounts/``) — pagination behavior differs from other user-related APIs, making it difficult for consumers to use a single data-loading pattern.
* **Course Members API** (``/api/courses/v1/.../members/``) — returns all enrollments without pagination, relying on a ``COURSE_MEMBER_API_ENROLLMENT_LIMIT`` setting (default 1000) to cap results and raising ``OverEnrollmentLimitException`` instead of paginating.
* **Enrollment API** (``/api/enrollment/v1/``) — some list endpoints return full result sets without pagination support.
* **Course Blocks API** (``/api/courses/v2/blocks/``) — intentionally returns unpaginated data for the entire course structure, which can result in very large response payloads.

Code example (target pagination usage)
---------------------------------------

**Example using DefaultPagination with a ListAPIView:**

.. code-block:: python

   # views.py
   from rest_framework.generics import ListAPIView
   from edx_rest_framework_extensions.paginators import DefaultPagination
   from .serializers import EnrollmentSerializer

   class EnrollmentListView(ListAPIView):
       """
       Returns a paginated list of enrollments for the authenticated user.

       Pagination parameters:
           - page (int): The page number to retrieve. Default is 1.
           - page_size (int): Number of results per page. Default is 10, max is 100.

       Response envelope:
           - count (int): Total number of results.
           - num_pages (int): Total number of pages.
           - current_page (int): The current page number.
           - next (str|null): URL for the next page, or null.
           - previous (str|null): URL for the previous page, or null.
           - start (int): The starting index of the current page.
           - results (list): The list of enrollment objects.
       """
       serializer_class = EnrollmentSerializer
       pagination_class = DefaultPagination

       def get_queryset(self):
           return CourseEnrollment.objects.filter(
               user=self.request.user,
               is_active=True,
           ).order_by('-created')

**Example subclassing DefaultPagination for a mobile endpoint with a smaller page size:**

.. code-block:: python

   # paginators.py
   from edx_rest_framework_extensions.paginators import DefaultPagination

   class MobileDefaultPagination(DefaultPagination):
       """
       Pagination tuned for mobile clients with smaller payloads.
       """
       page_size = 5
       max_page_size = 50

**Example using DefaultPagination with a plain APIView (manual invocation):**

.. code-block:: python

   # views.py
   from rest_framework.views import APIView
   from rest_framework.response import Response
   from edx_rest_framework_extensions.paginators import DefaultPagination

   class CompletionListView(APIView):
       pagination_class = DefaultPagination

       def get(self, request):
           completions = BlockCompletion.objects.filter(
               user=request.user
           ).order_by('-modified')
           paginator = self.pagination_class()
           page = paginator.paginate_queryset(completions, request)
           serializer = CompletionSerializer(page, many=True)
           return paginator.get_paginated_response(serializer.data)

Consequences
------------

Positive
~~~~~~~~

* External systems and AI agents can implement a single, reusable data loader for all Open edX list endpoints.
* Consumers can reliably pre-calculate batch sizes using the ``count`` and ``num_pages`` fields in every paginated response.
* Eliminates unbounded response sizes that currently risk overloading clients and timing out requests (e.g., large enrollment or discussion lists).
* Enables consistent OpenAPI schema generation for all list endpoints.
* Leverages the already-existing ``DefaultPagination`` class, minimizing new code.

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* Endpoints that currently return full result sets (e.g., Course Blocks API) will require consumers to implement pagination loops where they previously did not need to.
* Requires refactoring views that use ``APIView`` directly without DRF's generic pagination machinery.
* Migrating ``limit``/``offset`` endpoints to ``page``/``page_size`` is a breaking change for existing consumers of those specific endpoints and must be versioned.
* Some internal consumers (e.g., modulestore aggregation) may need to be updated to handle paginated results instead of full lists.

Alternatives Considered
-----------------------

* **Standardize on LimitOffsetPagination instead of PageNumberPagination**: Rejected because ``edx-drf-extensions`` already ships ``DefaultPagination`` based on ``PageNumberPagination``, and a significant portion of the platform already uses it. Additionally, ``limit``/``offset`` pagination degrades in performance with large offsets because the database must scan and skip all preceding rows, making it unsuitable for large Open edX datasets such as enrollments and completions.
* **Adopt CursorPagination as the platform standard**: Rejected because cursor-based pagination, while performant for large and frequently-changing datasets, does not support random page access (jumping to page N). This would break existing MFE patterns that display numbered page controls. Cursor pagination also requires a stable, unique, sequential sort key on every queryset, which not all Open edX models guarantee today.
* **Allow each API app to choose its own pagination style**: Rejected because this is the current state, and it is the root cause of the inconsistency this ADR aims to resolve.
* **Do nothing and document the differences**: Rejected because documentation alone does not reduce the integration burden on consumers or prevent future inconsistencies.

Rollout Plan
------------

1. Audit all list-type API endpoints in ``edx-platform`` to categorize them as: already using ``DefaultPagination``, using a different pagination class, or unpaginated.
2. Add a custom ``pylint`` or ``edx-lint`` check that warns when a ``ListAPIView`` or list-returning ``APIView`` does not specify ``DefaultPagination`` (or a subclass).
3. Migrate high-impact unpaginated endpoints first (Course Members, Completion, Enrollment).
4. Migrate ``limit``/``offset`` endpoints by introducing new API versions  that use ``DefaultPagination``, and deprecating the old versions.
5. Update MFEs and known external consumers to adopt the new pagination parameters where versions change.
6. Update API documentation and OpenAPI specs to reflect the standardized pagination envelope.

References
----------

* ``edx-drf-extensions`` ``DefaultPagination`` class: https://github.com/openedx/edx-drf-extensions/blob/master/edx_rest_framework_extensions/paginators.py
* Django REST Framework Pagination documentation: https://www.django-rest-framework.org/api-guide/pagination/
* Open edX REST API Standards: "Pagination" recommendations for API consistency.
* Open edX API Thoughts wiki: https://openedx.atlassian.net/wiki/spaces/AC/pages/16646635/API+Thoughts
