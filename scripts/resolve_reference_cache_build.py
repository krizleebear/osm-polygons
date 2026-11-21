#!/usr/bin/env python3
"""
Resolve the latest completed Azure DevOps build containing a specific artifact.

Queries the Azure DevOps Builds and Artifacts REST API using an OAuth token
(typically mapped from `$(System.AccessToken)`) so optional cache downloads via
`DownloadPipelineArtifact@2` can target an exact `pipelineId` or skip cleanly
without emitting `##[error]No builds currently exist...` when no cached build
or artifact exists yet.

Uses only the Python 3 standard library.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_VERSION = "7.0"
DEFAULT_MAX_BUILDS = 25
DEFAULT_TIMEOUT = 15

_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_target(spec: str) -> tuple[str, int]:
    """Parse and validate a `VAR_NAME=DEFINITION_ID` target specification."""
    if "=" not in spec:
        raise argparse.ArgumentTypeError(
            f"Invalid target '{spec}': expected VAR_NAME=DEFINITION_ID"
        )
    var_name, raw_def_id = spec.split("=", 1)
    if not _VAR_NAME_RE.match(var_name):
        raise argparse.ArgumentTypeError(
            f"Invalid variable name '{var_name}' in target '{spec}'"
        )
    try:
        definition_id = int(raw_def_id)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid definition ID '{raw_def_id}' in target '{spec}': must be a positive integer"
        ) from exc
    if definition_id <= 0:
        raise argparse.ArgumentTypeError(
            f"Invalid definition ID '{raw_def_id}' in target '{spec}': must be a positive integer"
        )
    return var_name, definition_id


def build_request_json(
    url: str, token: str, timeout: int = DEFAULT_TIMEOUT
) -> tuple[int, dict | None]:
    """Perform an authenticated HTTP GET and return `(status_code, decoded_json)`."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 404, None
        raise


def find_latest_build_with_artifact(
    collection_uri: str,
    project: str,
    definition_id: int,
    artifact_name: str,
    token: str,
    *,
    exclude_build_id: int | None = None,
    max_builds: int = DEFAULT_MAX_BUILDS,
    timeout: int = DEFAULT_TIMEOUT,
    requester=build_request_json,
) -> tuple[dict | None, list[int]]:
    """Return `(build_dict, skipped_ids)` for the newest completed build holding `artifact_name`."""
    base = (
        f"{collection_uri.rstrip('/')}/{urllib.parse.quote(project, safe='')}"
        "/_apis/build/builds"
    )
    builds_url = (
        f"{base}?definitions={definition_id}"
        f"&statusFilter=completed"
        f"&queryOrder=finishTimeDescending"
        f"&$top={max_builds}"
        f"&api-version={API_VERSION}"
    )
    status, data = requester(builds_url, token, timeout=timeout)
    if status != 200 or not isinstance(data, dict):
        return None, []

    encoded_artifact = urllib.parse.quote(artifact_name, safe="")
    skipped_ids: list[int] = []
    for build in data.get("value") or []:
        if not isinstance(build, dict):
            continue
        raw_id = build.get("id")
        if raw_id is None or build.get("deleted"):
            continue
        build_id = int(raw_id)
        if exclude_build_id is not None and build_id == exclude_build_id:
            continue

        artifact_url = (
            f"{base}/{build_id}/artifacts"
            f"?artifactName={encoded_artifact}&api-version={API_VERSION}"
        )
        art_status, art_data = requester(artifact_url, token, timeout=timeout)
        if (
            art_status == 200
            and isinstance(art_data, dict)
            and art_data.get("name") == artifact_name
            and art_data.get("resource")
        ):
            return build, skipped_ids
        skipped_ids.append(build_id)

    return None, skipped_ids


def resolve_targets(
    collection_uri: str,
    project: str,
    artifact_name: str,
    targets: list[tuple[str, int]],
    *,
    exclude_build_id: int | None = None,
    max_builds: int = DEFAULT_MAX_BUILDS,
    timeout: int = DEFAULT_TIMEOUT,
    token_env: str = "SYSTEM_ACCESSTOKEN",
    requester=build_request_json,
) -> int:
    """Resolve all `VAR_NAME=DEFINITION_ID` targets and emit Azure Pipelines variables."""
    token = os.environ.get(token_env, "").strip()
    if not token:
        print(
            f"##vso[task.logissue type=warning]Environment variable '{token_env}' "
            "is empty; skipping optional reference cache lookup."
        )
        for var_name, _ in targets:
            print(f"##vso[task.setvariable variable={var_name}]")
        return 0

    for var_name, definition_id in targets:
        try:
            build, skipped_ids = find_latest_build_with_artifact(
                collection_uri,
                project,
                definition_id,
                artifact_name,
                token,
                exclude_build_id=exclude_build_id,
                max_builds=max_builds,
                timeout=timeout,
                requester=requester,
            )
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
        ) as exc:
            print(
                f"##vso[task.logissue type=warning]Could not query cached artifact "
                f"'{artifact_name}' for definition {definition_id}: {exc}; "
                "proceeding without this cache."
            )
            print(f"##vso[task.setvariable variable={var_name}]")
            continue

        if build is None:
            print(
                f"[INFO] No completed build among latest {len(skipped_ids)} build(s) "
                f"in definition {definition_id} contains artifact '{artifact_name}'; "
                "skipping optional cache download."
            )
            print(f"##vso[task.setvariable variable={var_name}]")
            continue

        build_id = int(build["id"])
        build_number = build.get("buildNumber", "unknown")
        result = build.get("result", "unknown")
        skipped_note = (
            f" after skipping {len(skipped_ids)} build(s) without it ({skipped_ids})"
            if skipped_ids
            else ""
        )
        print(
            f"[INFO] Resolved {var_name}={build_id} (definition {definition_id}, "
            f"buildNumber={build_number}, result={result}) containing artifact "
            f"'{artifact_name}'{skipped_note}"
        )
        print(f"##vso[task.setvariable variable={var_name}]{build_id}")

    return 0


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Resolve latest Azure DevOps builds containing a pipeline artifact."
    )
    parser.add_argument(
        "--collection-uri",
        required=True,
        help="Azure DevOps collection URI (e.g. $(System.CollectionUri))",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Azure DevOps project ID or name (e.g. $(System.TeamProjectId))",
    )
    parser.add_argument(
        "--artifact",
        default="admin-polygons-reference",
        help="Pipeline artifact name to probe (default: admin-polygons-reference)",
    )
    parser.add_argument(
        "--exclude-build-id",
        default=None,
        help="Optional current build ID to exclude from candidate builds",
    )
    parser.add_argument(
        "--max-builds",
        type=int,
        default=DEFAULT_MAX_BUILDS,
        help=f"Maximum completed builds to scan per definition (default: {DEFAULT_MAX_BUILDS})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--token-env",
        default="SYSTEM_ACCESSTOKEN",
        help="Environment variable containing the OAuth Bearer token (default: SYSTEM_ACCESSTOKEN)",
    )
    parser.add_argument(
        "--target",
        action="append",
        required=True,
        type=parse_target,
        help="Target mapping VAR_NAME=DEFINITION_ID (can be specified multiple times)",
    )
    args = parser.parse_args(argv)

    exclude_id = None
    if args.exclude_build_id is not None:
        raw_exclude = args.exclude_build_id.strip()
        if raw_exclude.isdigit():
            exclude_id = int(raw_exclude)

    return resolve_targets(
        args.collection_uri,
        args.project,
        args.artifact,
        args.target,
        exclude_build_id=exclude_id,
        max_builds=args.max_builds,
        timeout=args.timeout,
        token_env=args.token_env,
    )


if __name__ == "__main__":
    sys.exit(main())
