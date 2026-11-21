#!/usr/bin/env python3
"""Unit tests for scripts/resolve_reference_cache_build.py."""

import argparse
import contextlib
import io
import os
import unittest
import urllib.error
from unittest import mock

import resolve_reference_cache_build as resolver


def make_requester(responses):
    """Build a fake `requester(url, token, timeout=...)` backed by a mapping."""
    calls = []

    def _fake(url, token, timeout=15):
        calls.append((url, token, timeout))
        if url not in responses:
            raise AssertionError(f"Unexpected URL requested: {url}")
        entry = responses[url]
        if isinstance(entry, BaseException):
            raise entry
        return entry

    _fake.calls = calls
    return _fake


class TestResolveReferenceCacheBuild(unittest.TestCase):
    COLLECTION = "https://dev.azure.com/org/"
    PROJECT = "proj-uuid"
    ARTIFACT = "admin-polygons-reference"

    def _builds_url(self, definition_id, top=25):
        return (
            f"https://dev.azure.com/org/{self.PROJECT}/_apis/build/builds"
            f"?definitions={definition_id}&statusFilter=completed"
            f"&queryOrder=finishTimeDescending&$top={top}"
            f"&api-version={resolver.API_VERSION}"
        )

    def _artifact_url(self, build_id, artifact=ARTIFACT):
        return (
            f"https://dev.azure.com/org/{self.PROJECT}/_apis/build/builds/"
            f"{build_id}/artifacts?artifactName={artifact}"
            f"&api-version={resolver.API_VERSION}"
        )

    def test_parse_target_valid_and_invalid(self):
        self.assertEqual(resolver.parse_target("DL_REF_BUILD_ID=9"), ("DL_REF_BUILD_ID", 9))
        self.assertEqual(resolver.parse_target("_var2=42"), ("_var2", 42))

        for bad in ("missing_equals", "123BAD=4", "BAD-NAME=4", "VAR=abc", "VAR=0", "VAR=-3"):
            with self.subTest(bad=bad):
                with self.assertRaises(argparse.ArgumentTypeError):
                    resolver.parse_target(bad)

    def test_newest_completed_build_has_artifact(self):
        requester = make_requester(
            {
                self._builds_url(9): (
                    200,
                    {"value": [{"id": 501, "buildNumber": "20261121.1", "result": "succeeded"}]},
                ),
                self._artifact_url(501): (
                    200,
                    {"name": self.ARTIFACT, "resource": {"type": "PipelineArtifact"}},
                ),
            }
        )
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"SYSTEM_ACCESSTOKEN": "tok-123"}, clear=False):
            with contextlib.redirect_stdout(stdout):
                rc = resolver.resolve_targets(
                    self.COLLECTION,
                    self.PROJECT,
                    self.ARTIFACT,
                    [("DL_REF_BUILD_ID", 9)],
                    requester=requester,
                )
        self.assertEqual(rc, 0)
        out = stdout.getvalue()
        self.assertIn("##vso[task.setvariable variable=DL_REF_BUILD_ID]501", out)
        self.assertNotIn("##vso[task.logissue", out)
        self.assertNotIn("##[error]", out)

    def test_skips_builds_missing_artifact_and_selects_older_build(self):
        requester = make_requester(
            {
                self._builds_url(4): (
                    200,
                    {
                        "value": [
                            {"id": 900, "buildNumber": "v3.8.fail", "result": "failed"},
                            {"id": 899, "buildNumber": "v3.8.old", "result": "partiallySucceeded"},
                            {"id": 898, "buildNumber": "v3.8.ok", "result": "succeeded"},
                        ]
                    },
                ),
                self._artifact_url(900): (404, None),
                self._artifact_url(899): (200, {"name": self.ARTIFACT, "resource": None}),
                self._artifact_url(898): (
                    200,
                    {"name": self.ARTIFACT, "resource": {"type": "PipelineArtifact"}},
                ),
            }
        )
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"SYSTEM_ACCESSTOKEN": "tok-123"}, clear=False):
            with contextlib.redirect_stdout(stdout):
                rc = resolver.resolve_targets(
                    self.COLLECTION,
                    self.PROJECT,
                    self.ARTIFACT,
                    [("POLY_REF_BUILD_ID", 4)],
                    requester=requester,
                )
        self.assertEqual(rc, 0)
        out = stdout.getvalue()
        self.assertIn("##vso[task.setvariable variable=POLY_REF_BUILD_ID]898", out)
        self.assertIn("skipping 2 build(s)", out)
        self.assertNotIn("##vso[task.logissue", out)

    def test_excludes_current_and_deleted_builds(self):
        requester = make_requester(
            {
                self._builds_url(4): (
                    200,
                    {
                        "value": [
                            {"id": 1000, "buildNumber": "current", "result": "succeeded"},
                            {"id": 999, "buildNumber": "deleted", "result": "succeeded", "deleted": True},
                            {"id": 998, "buildNumber": "prev", "result": "succeeded"},
                        ]
                    },
                ),
                self._artifact_url(998): (
                    200,
                    {"name": self.ARTIFACT, "resource": {"type": "PipelineArtifact"}},
                ),
            }
        )
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"SYSTEM_ACCESSTOKEN": "tok-123"}, clear=False):
            with contextlib.redirect_stdout(stdout):
                rc = resolver.resolve_targets(
                    self.COLLECTION,
                    self.PROJECT,
                    self.ARTIFACT,
                    [("POLY_REF_BUILD_ID", 4)],
                    exclude_build_id=1000,
                    requester=requester,
                )
        self.assertEqual(rc, 0)
        requested_urls = [u for u, _, _ in requester.calls]
        self.assertNotIn(self._artifact_url(1000), requested_urls)
        self.assertNotIn(self._artifact_url(999), requested_urls)
        self.assertIn("##vso[task.setvariable variable=POLY_REF_BUILD_ID]998", stdout.getvalue())

    def test_zero_builds_or_all_404_sets_empty_without_issues(self):
        for scenario_name, responses in (
            ("no_builds", {self._builds_url(4): (200, {"value": []})}),
            (
                "all_404",
                {
                    self._builds_url(4): (200, {"value": [{"id": 101}, {"id": 100}]}),
                    self._artifact_url(101): (404, None),
                    self._artifact_url(100): (404, None),
                },
            ),
        ):
            with self.subTest(scenario=scenario_name):
                requester = make_requester(responses)
                stdout = io.StringIO()
                with mock.patch.dict(os.environ, {"SYSTEM_ACCESSTOKEN": "tok-123"}, clear=False):
                    with contextlib.redirect_stdout(stdout):
                        rc = resolver.resolve_targets(
                            self.COLLECTION,
                            self.PROJECT,
                            self.ARTIFACT,
                            [("POLY_REF_BUILD_ID", 4)],
                            requester=requester,
                        )
                self.assertEqual(rc, 0)
                out = stdout.getvalue()
                self.assertIn("[INFO]", out)
                self.assertIn("##vso[task.setvariable variable=POLY_REF_BUILD_ID]\n", out)
                self.assertNotIn("##[error]", out)
                self.assertNotIn("##vso[task.logissue", out)

    def test_unexpected_errors_and_missing_token_warn_and_set_empty(self):
        # 1. Missing token
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"SYSTEM_ACCESSTOKEN": ""}, clear=False):
            with contextlib.redirect_stdout(stdout):
                rc = resolver.resolve_targets(
                    self.COLLECTION,
                    self.PROJECT,
                    self.ARTIFACT,
                    [("DL_REF_BUILD_ID", 9), ("POLY_REF_BUILD_ID", 4)],
                )
        self.assertEqual(rc, 0)
        out = stdout.getvalue()
        self.assertEqual(out.count("##vso[task.logissue type=warning]"), 1)
        self.assertIn("##vso[task.setvariable variable=DL_REF_BUILD_ID]\n", out)
        self.assertIn("##vso[task.setvariable variable=POLY_REF_BUILD_ID]\n", out)

        # 2. HTTP 500 and URLError still process remaining targets independently
        http_500 = urllib.error.HTTPError(
            self._builds_url(9), 500, "Internal Server Error", hdrs=None, fp=None
        )
        url_err = urllib.error.URLError("timed out")
        for exc in (http_500, url_err):
            with self.subTest(exc=type(exc).__name__):
                requester = make_requester(
                    {
                        self._builds_url(9): exc,
                        self._builds_url(4): (200, {"value": [{"id": 77}]}),
                        self._artifact_url(77): (
                            200,
                            {"name": self.ARTIFACT, "resource": {"type": "PipelineArtifact"}},
                        ),
                    }
                )
                stdout = io.StringIO()
                with mock.patch.dict(os.environ, {"SYSTEM_ACCESSTOKEN": "tok"}, clear=False):
                    with contextlib.redirect_stdout(stdout):
                        rc = resolver.resolve_targets(
                            self.COLLECTION,
                            self.PROJECT,
                            self.ARTIFACT,
                            [("DL_REF_BUILD_ID", 9), ("POLY_REF_BUILD_ID", 4)],
                            requester=requester,
                        )
                self.assertEqual(rc, 0)
                out = stdout.getvalue()
                self.assertIn("##vso[task.logissue type=warning]", out)
                self.assertIn("##vso[task.setvariable variable=DL_REF_BUILD_ID]\n", out)
                self.assertIn("##vso[task.setvariable variable=POLY_REF_BUILD_ID]77\n", out)

    def test_multiple_targets_one_found_one_missing(self):
        requester = make_requester(
            {
                self._builds_url(9): (200, {"value": [{"id": 300, "buildNumber": "dl-300", "result": "succeeded"}]}),
                self._artifact_url(300): (
                    200,
                    {"name": self.ARTIFACT, "resource": {"type": "PipelineArtifact"}},
                ),
                self._builds_url(4): (200, {"value": [{"id": 400}]}),
                self._artifact_url(400): (404, None),
            }
        )
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"SYSTEM_ACCESSTOKEN": "tok"}, clear=False):
            with contextlib.redirect_stdout(stdout):
                rc = resolver.resolve_targets(
                    self.COLLECTION,
                    self.PROJECT,
                    self.ARTIFACT,
                    [("DL_REF_BUILD_ID", 9), ("POLY_REF_BUILD_ID", 4)],
                    requester=requester,
                )
        self.assertEqual(rc, 0)
        out = stdout.getvalue()
        self.assertIn("##vso[task.setvariable variable=DL_REF_BUILD_ID]300\n", out)
        self.assertIn("##vso[task.setvariable variable=POLY_REF_BUILD_ID]\n", out)
        self.assertNotIn("##vso[task.logissue", out)

    def test_cli_main_ignores_non_numeric_exclude_build_id(self):
        fake_resp = mock.MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = b'{"value": []}'
        fake_resp.__enter__.return_value = fake_resp
        fake_resp.__exit__.return_value = False

        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"SYSTEM_ACCESSTOKEN": "tok"}, clear=False):
            with mock.patch("urllib.request.urlopen", return_value=fake_resp):
                with contextlib.redirect_stdout(stdout):
                    rc = resolver.main(
                        [
                            "--collection-uri",
                            self.COLLECTION,
                            "--project",
                            self.PROJECT,
                            "--exclude-build-id",
                            "",
                            "--target",
                            "DL_REF_BUILD_ID=9",
                        ]
                    )
        self.assertEqual(rc, 0)
        self.assertIn("##vso[task.setvariable variable=DL_REF_BUILD_ID]\n", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
