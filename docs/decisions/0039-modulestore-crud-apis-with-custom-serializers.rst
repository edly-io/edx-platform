ADR-016: Provide Modulestore CRUD APIs via Custom DRF Layers
============================================================

:Status: Proposed
:Date: 2026-04-08
:Deciders: API Working Group

Context
=======

Open edX currently lacks comprehensive REST APIs to create, view, update, and delete modulestore
entities (courses, blocks). Modulestore is not backed by standard Django models, so DRF cannot be
applied with model serializers directly.

Decision
========

Implement modulestore APIs using DRF **with custom serializers and service methods**:

1. Create DRF ViewSets for modulestore resources (course, block).
2. Use explicit, non-model serializers for validation and representation.
3. Enforce permissions and visibility rules appropriate for authoring roles.
4. Provide OpenAPI schemas and examples for all operations.

Relevance in edx-platform
=========================

* **Modulestore is not ORM-backed**: Courses and blocks live in modulestore
  (MongoDB/split); ``xmodule.modulestore`` exposes ``get_course()``, ``get_item()``,
  ``update_item()``, etc. DRF model serializers do not apply directly.
* **Existing read-only APIs**: ``openedx/core/djangoapps/olx_rest_api/views.py``
  uses ``@api_view(['GET'])`` and ``view_auth_classes()``, calls
  ``modulestore().get_item()`` and ``serialize_modulestore_block_for_learning_core()``,
  and returns a custom JSON shape (no ModelSerializer). Contentstore course API
  (``cms/djangoapps/contentstore/api/views/utils.py``) uses ``BaseCourseView`` and
  ``modulestore().get_course()`` with custom depth handling.
* **Studio/course authoring**: Contentstore views (e.g. ``contentstore/views/block.py``,
  ``course.py``) perform create/update/delete via Python APIs, not REST; this ADR
  proposes exposing CRUD via DRF with custom serializers and service-layer methods.

Code examples
=============

**Custom serializer (no model):**

.. code-block:: python

   from rest_framework import serializers

   class ModulestoreBlockSerializer(serializers.Serializer):
       id = serializers.CharField(read_only=True)
       block_type = serializers.CharField()
       display_name = serializers.CharField(required=False)
       parent = serializers.CharField(required=False)

       def create(self, validated_data):
           return modulestore_service.create_block(
               self.context["course_key"], validated_data
           )

       def update(self, instance, validated_data):
           return modulestore_service.update_block(instance, validated_data)

**ViewSet with custom get_queryset / get_object:**

.. code-block:: python

   from rest_framework import viewsets
   from openedx.core.lib.api.view_utils import view_auth_classes

   @view_auth_classes()
   class ModulestoreBlockViewSet(viewsets.ModelViewSet):
       serializer_class = ModulestoreBlockSerializer
       permission_classes = [IsAuthenticated, HasStudioWriteAccess]

       def get_object(self):
           usage_key = UsageKey.from_string(self.kwargs["usage_key"])
           if not has_studio_read_access(self.request.user, usage_key.course_key):
               raise PermissionDenied()
           return modulestore().get_item(usage_key)

       def get_queryset(self):
           course_key = CourseKey.from_string(self.kwargs["course_id"])
           return modulestore().get_items(course_key, ...)  # or service method

   # Router: /api/modulestore/courses/<course_id>/blocks/ list, retrieve, create, update, destroy

**Service layer (recommended):**

.. code-block:: python

   # services.py
   def get_block(usage_key, user):
       if not has_studio_read_access(user, usage_key.course_key):
           raise PermissionDenied()
       return modulestore().get_item(usage_key)

   def update_block(usage_key, user, data):
       block = get_block(usage_key, user)
       if not has_studio_write_access(user, usage_key.course_key):
           raise PermissionDenied()
       # Apply data to block, then modulestore().update_item(...)
       return block

Consequences
============

* Pros

  * Enables cleaner authoring/integration flows for Studio/MFEs and external tools.
  * Standardizes modulestore interactions behind documented REST APIs.

* Cons / Costs

  * Significant implementation effort; careful security/authorization required.
  * Backing store migrations must be abstracted behind stable service interfaces.

Implementation Notes
====================

* Start with read-only endpoints (GET) for course structure/blocks, then add write operations.
* Ensure stable contracts independent of modulestore backend.

References
==========

* “Modulestore APIs” recommendation in the Open edX REST API standardization notes.
