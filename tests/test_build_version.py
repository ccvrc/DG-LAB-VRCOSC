"""Exercise build identity generation without modifying the source version."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="PowerShell is not installed")


def generate(tmp_path, overrides):
    version_file = tmp_path / "version.py"
    version_file.write_text('VERSION = "v1.2.3"\n', encoding="utf-8")
    metadata_file = tmp_path / "build-info.json"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GITHUB_")}
    environment.update({
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "ccvrc/DG-LAB-VRCOSC",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF_TYPE": "branch",
        "GITHUB_REF": "refs/heads/master",
        "GITHUB_REF_NAME": "master",
        "GITHUB_RUN_ID": "9876543210",
        "GITHUB_RUN_NUMBER": "42",
        "GITHUB_RUN_ATTEMPT": "2",
    })
    environment.update(overrides)
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(PROJECT_ROOT / "generate_version.ps1"), "-OutputFile", str(version_file),
         "-MetadataFile", str(metadata_file)],
        cwd=PROJECT_ROOT, env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    metadata = json.loads(metadata_file.read_text(encoding="utf-8")) if metadata_file.exists() else None
    return result, metadata, version_file.read_text(encoding="utf-8")


def test_master_push_has_sortable_actions_identity(tmp_path):
    result, metadata, source = generate(tmp_path, {})
    assert result.returncode == 0, result.stderr
    assert metadata["channel"] == "actions"
    assert metadata["version"] == "v1.2.3.dev42"
    assert metadata["run_id"] == 9876543210
    assert metadata["run_number"] == 42
    assert metadata["run_attempt"] == 2
    assert metadata["branch"] == "master"
    assert metadata["workflow"] == "build-python-app.yml"
    assert len(metadata["commit"]) == 40
    assert source == 'VERSION = "v1.2.3.dev42"\n'


def test_stable_tag_preserves_the_declared_release_version(tmp_path):
    result, metadata, _ = generate(tmp_path, {
        "GITHUB_REF_TYPE": "tag", "GITHUB_REF": "refs/tags/v1.2.3", "GITHUB_REF_NAME": "v1.2.3",
    })
    assert result.returncode == 0, result.stderr
    assert metadata["channel"] == "stable"
    assert metadata["version"] == "v1.2.3"
    assert metadata["branch"] == "v1.2.3"


@pytest.mark.parametrize("overrides", [
    {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/7/merge", "GITHUB_REF_NAME": "7/merge"},
    {"GITHUB_REF": "refs/heads/feature/demo", "GITHUB_REF_NAME": "feature/demo"},
    {"GITHUB_EVENT_NAME": "workflow_dispatch"},
])
def test_non_release_events_never_claim_a_published_channel(tmp_path, overrides):
    result, metadata, _ = generate(tmp_path, overrides)
    assert result.returncode == 0, result.stderr
    assert metadata["channel"] == "local"


@pytest.mark.parametrize("tag", ["v9.9.9", "v1.2.3-beta", "v1.2.3;echo bad"])
def test_invalid_or_mismatched_release_tag_fails_before_writing(tmp_path, tag):
    result, metadata, source = generate(tmp_path, {
        "GITHUB_REF_TYPE": "tag", "GITHUB_REF": "refs/tags/" + tag, "GITHUB_REF_NAME": tag,
    })
    assert result.returncode != 0
    assert metadata is None
    assert source == 'VERSION = "v1.2.3"\n'


def test_local_build_is_identified_as_local(tmp_path):
    result, metadata, _ = generate(tmp_path, {"GITHUB_ACTIONS": "false"})
    assert result.returncode == 0, result.stderr
    assert metadata["channel"] == "local"
    assert metadata["version"].startswith("v1.2.3+local.")
    assert metadata["run_id"] == 0
