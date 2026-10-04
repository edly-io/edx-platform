"""
Contract tests shared by the course video APIs.
"""
import functools
import json
import os
import subprocess
import sys
from pathlib import Path

import ddt
from django.test import TestCase
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings

import cms.envs
from cms.lib.spectacular import cms_api_filter

LEGACY_USAGE_PATH = "/api/contentstore/v1/videos/{course_id}/{edx_video_id}/usage"
LEGACY_DOWNLOAD_PATH = "/api/contentstore/v1/videos/{course_id}/download"

ENUM_POSTPROCESSING_HOOK = "drf_spectacular.hooks.postprocess_schema_enums"
MIGRATED_PATHS_HOOK = "cms.lib.spectacular.cms_mark_migrated_paths"

PRODUCTION_SETTINGS = "cms.envs.production"
DEVSTACK_SETTINGS = "cms.envs.devstack"
SETTINGS_DIRECTORY = Path(cms.envs.__file__).resolve().parent
REPO_ROOT = SETTINGS_DIRECTORY.parents[1]
MOCK_CONFIG = SETTINGS_DIRECTORY / "mock.yml"

# The schema settings a document generated in this process has to be given for
# its addresses to come out the way the deployment publishes them.
GENERATION_SETTING_KEYS = ("PREPROCESSING_HOOKS", "POSTPROCESSING_HOOKS", "SCHEMA_PATH_PREFIX")
SETTINGS_MARKER = "schema settings: "


@functools.lru_cache
def schema_settings(module):
    """
    Return ``SPECTACULAR_SETTINGS`` of the deployment settings module ``module``.

    Deployment settings share their mutable defaults with the settings the test
    process runs under and would alter them on import, so they are read in a
    separate process.
    """
    script = (
        "import importlib, json, sys\n"
        "values = importlib.import_module(sys.argv[1]).SPECTACULAR_SETTINGS\n"
        "print(sys.argv[2] + json.dumps(values))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, module, SETTINGS_MARKER],
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
        env={**os.environ, "CMS_CFG": str(MOCK_CONFIG), "SERVICE_VARIANT": "cms"},
        text=True,
    )
    reported = next(line for line in completed.stdout.splitlines() if line.startswith(SETTINGS_MARKER))
    return json.loads(reported[len(SETTINGS_MARKER):])


def generate_document(postprocessing_hooks=None, generator=None):
    """Generate the Studio document with the production schema settings."""
    configured = schema_settings(PRODUCTION_SETTINGS)
    overrides = {key: configured[key] for key in GENERATION_SETTING_KEYS}
    if postprocessing_hooks is not None:
        overrides["POSTPROCESSING_HOOKS"] = postprocessing_hooks
    with patched_settings(overrides):
        return (generator or SchemaGenerator()).get_schema(request=None, public=True)


@functools.lru_cache
def production_document():
    return generate_document()


def operations(document):
    """Yield (path, method, operation) for every operation of ``document``."""
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if isinstance(operation, dict) and "operationId" in operation:
                yield path, method, operation


class CmsSchemaHookTest(TestCase):
    """The schema hooks admit the new addresses."""

    def test_filter_admits_the_authoring_prefix(self):
        admitted = cms_api_filter([
            ("/api/authoring/v2/courses/course-v1:a+b+c/video_archives/", None, "post", None),
            ("/api/contentstore/v1/videos/course-v1:a+b+c/download", None, "put", None),
            ("/api/authoring/v2", None, "get", None),
            ("/login", None, "get", None),
        ])

        assert [entry[0] for entry in admitted] == [
            "/api/authoring/v2/courses/course-v1:a+b+c/video_archives/",
            "/api/contentstore/v1/videos/course-v1:a+b+c/download",
        ]

    def test_filter_refuses_the_versioned_paths_of_other_apis(self):
        admitted = cms_api_filter([
            ("/api/xblock/v2/xblocks/lb:Org:lib:html:abc/", None, "get", None),
            ("/api/content_tagging/v1/taxonomies/", None, "get", None),
            ("/api/libraries/v2/", None, "get", None),
            ("/api/courses/v1/courses/", None, "get", None),
            ("/api/authoring_tools/v1/", None, "get", None),
            ("/api/v1/authoring/", None, "get", None),
        ])

        assert not admitted, admitted

    def test_filter_admits_the_course_discussions_switch_and_nothing_else_under_courses(self):
        admitted = cms_api_filter([
            ("/api/courses/{course_key_string}/bulk_enable_disable_discussions", None, "put", None),
            ("/api/courses/{course_key_string}/updates", None, "get", None),
            ("/api/discussions/v0/bulk_enable_disable_discussions", None, "get", None),
        ])

        assert [entry[0] for entry in admitted] == [
            "/api/courses/{course_key_string}/bulk_enable_disable_discussions",
        ]


class CmsSchemaDocumentTest(TestCase):
    """The published Studio document, generated with the production schema settings."""

    def test_only_the_studio_apis_are_published(self):
        paths = list(production_document()["paths"])

        assert {path.split("/")[2] for path in paths} == {"contentstore", "courses"}
        assert [path for path in paths if path.startswith("/api/courses/")] == [
            "/api/courses/{course_key_string}/bulk_enable_disable_discussions",
        ]

    def test_operation_ids(self):
        document = production_document()

        assert document["paths"][LEGACY_USAGE_PATH]["get"]["operationId"] == "v1_videos_usage_retrieve"
        assert document["paths"][LEGACY_DOWNLOAD_PATH]["put"]["operationId"] == "v1_videos_download_update"

    @staticmethod
    def marks(document, path, method):
        operation = document["paths"][path][method]
        return operation.get("deprecated"), operation.get("x-internal")

    def test_no_operation_is_flagged_before_its_successor_exists(self):
        document = production_document()

        flagged = [
            (path, method)
            for path, method, __ in operations(document)
            if self.marks(document, path, method) != (None, None)
        ]
        assert flagged == []

    def test_enum_components_survive_the_registered_post_processing(self):
        without_enum_hook = generate_document(postprocessing_hooks=[MIGRATED_PATHS_HOOK])

        assert not [name for name in without_enum_hook["components"]["schemas"] if name.endswith("Enum")]
        assert "ContentTypeEnum" in production_document()["components"]["schemas"]


@ddt.ddt
class CmsSchemaSettingsTest(TestCase):
    """The deployment settings publish full paths, and no server adds an API prefix of its own."""

    @ddt.data(PRODUCTION_SETTINGS, DEVSTACK_SETTINGS)
    def test_paths_are_published_in_full(self, module):
        configured = schema_settings(module)

        assert "SCHEMA_PATH_PREFIX_TRIM" not in configured
        assert configured["SCHEMA_PATH_PREFIX"] == r"/api/(contentstore|authoring)"

    @ddt.data(PRODUCTION_SETTINGS, DEVSTACK_SETTINGS)
    def test_the_post_processing_hooks_keep_the_enum_hook(self, module):
        hooks = schema_settings(module)["POSTPROCESSING_HOOKS"]

        assert [hook for hook in hooks if hook in (ENUM_POSTPROCESSING_HOOK, MIGRATED_PATHS_HOOK)] == [
            ENUM_POSTPROCESSING_HOOK,
            MIGRATED_PATHS_HOOK,
        ]

    # The Public server is AUTHORING_API_URL as configured. Whether a gateway
    # behind it serves the full paths is not decided by these settings and is
    # not checked here.
    @ddt.data(PRODUCTION_SETTINGS, DEVSTACK_SETTINGS)
    def test_no_server_url_carries_an_api_prefix(self, module):
        servers = schema_settings(module)["SERVERS"]

        assert [server["description"] for server in servers] == ["Public", "Local"]
        assert [server for server in servers if "/api/" in server["url"]] == []
