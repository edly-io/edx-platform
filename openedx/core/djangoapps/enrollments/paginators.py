"""
Paginators for the course enrollment related views.
"""


from edx_rest_framework_extensions.paginators import DefaultPagination  # ADR 0032


class CourseEnrollmentsApiListPagination(DefaultPagination):
    """
    ADR 0032 – standard pagination for the admin enrollments list API
    (GET /api/enrollment/v1/enrollments).

    Extends DefaultPagination with a larger default page size appropriate
    for an admin-facing, bulk-query endpoint.  The full 7-field response
    envelope (count, num_pages, current_page, start, next, previous,
    results) is provided by DefaultPagination.get_paginated_response.
    """
    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 100
