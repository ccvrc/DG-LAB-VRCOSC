"""Discover installable updates from the project's public GitHub releases.

Actions builds are published by the trusted master-branch workflow as
prereleases. This deliberately avoids Actions artifact downloads, which require
a GitHub login even when the repository is public.
"""

import json
import logging
import re
from urllib.parse import unquote, urlparse

import aiohttp
from packaging.version import Version


logger = logging.getLogger(__name__)
REPOSITORY = "ccvrc/DG-LAB-VRCOSC"
WORKFLOW = "build-python-app.yml"
BUILD_BRANCH = "master"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
LATEST_RELEASE_URL = f"{RELEASES_URL}/latest"
MAX_RELEASE_PAGES = 5
RELEASES_PER_PAGE = 100
MAX_METADATA_BYTES = 64 * 1024
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
_VERSION_PARTS = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
_STABLE_TAG = re.compile(rf"v?{_VERSION_PARTS}(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z")
_CURRENT_VERSION = re.compile(rf"v?{_VERSION_PARTS}(?=$|[-.+])")
_ACTIONS_VERSION = re.compile(rf"v?{_VERSION_PARTS}\.dev([1-9]\d*)\Z")
_BUILD_TAG = re.compile(r"build-([1-9]\d*)-([1-9]\d*)\Z")
_COMMIT = re.compile(r"[0-9a-fA-F]{40}\Z")


def _stable_version(tag):
    if not isinstance(tag, str) or not _STABLE_TAG.fullmatch(tag):
        return None
    # Build metadata has no precedence under SemVer.
    return Version(tag.split("+", 1)[0])


def _base_version(value):
    match = _CURRENT_VERSION.match(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"Invalid application version: {value!r}")
    return Version(".".join(match.groups()))


def _positive_int(value):
    if isinstance(value, bool):
        raise ValueError("Build run identifiers must be positive integers")
    if isinstance(value, str) and re.fullmatch(r"[1-9]\d*", value):
        return int(value)
    if isinstance(value, int) and value > 0:
        return value
    raise ValueError("Build run identifiers must be positive integers")


def _asset_url(asset, tag):
    if not isinstance(asset, dict):
        return None
    name, url = asset.get("name"), asset.get("browser_download_url")
    if not isinstance(name, str) or not isinstance(url, str):
        return None
    parsed = urlparse(url)
    expected_path = f"/{REPOSITORY}/releases/download/{tag}/{name}"
    if (parsed.scheme != "https" or parsed.netloc != "github.com"
            or parsed.query or parsed.fragment
            or unquote(parsed.path) != expected_path
            or "/" in name or "\\" in name):
        return None
    return url


def get_download_url(release_info):
    """Return the package URL, rejecting mirrors and unrelated repositories."""
    if not isinstance(release_info, dict):
        raise ValueError("Invalid GitHub release")
    tag = release_info.get("tag_name")
    for asset in release_info.get("assets", []):
        if not isinstance(asset, dict):
            continue
        name = asset.get("name", "")
        if (isinstance(name, str) and name.endswith(".zip")
                and (name == "DG-LAB-VRCOSC.zip" or name.startswith("DG-LAB-VRCOSC-"))):
            url = _asset_url(asset, tag)
            if url:
                return url
    raise ValueError("未找到来自 GitHub 官方仓库的 DG-LAB-VRCOSC 更新包")


def _installable(release):
    if not isinstance(release, dict) or release.get("draft") is not False:
        return False
    if not isinstance(release.get("assets"), list):
        return False
    try:
        get_download_url(release)
    except ValueError:
        return False
    return True


def _validate_build_info(info, tag):
    if not isinstance(info, dict):
        raise ValueError("Invalid build metadata")
    match = _BUILD_TAG.fullmatch(tag)
    version_match = _ACTIONS_VERSION.fullmatch(info.get("version", "")) if isinstance(info.get("version"), str) else None
    if (match is None or version_match is None
            or info.get("channel") != "actions"
            or info.get("repository") != REPOSITORY
            or info.get("workflow") != WORKFLOW
            or info.get("branch") != BUILD_BRANCH
            or not isinstance(info.get("commit"), str)
            or not _COMMIT.fullmatch(info["commit"])):
        raise ValueError("Build metadata does not identify an official master build")
    normalized = dict(info)
    for key in ("run_id", "run_number", "run_attempt"):
        normalized[key] = _positive_int(info.get(key))
    if ((normalized["run_id"], normalized["run_attempt"]) != tuple(map(int, match.groups()))
            or normalized["run_number"] != int(version_match.group(4))):
        raise ValueError("Build metadata does not match its release tag/version")
    return normalized


async def _list_releases(session):
    releases = []
    for page in range(1, MAX_RELEASE_PAGES + 1):
        async with session.get(RELEASES_URL, params={"per_page": RELEASES_PER_PAGE, "page": page}) as response:
            response.raise_for_status()
            batch = await response.json()
        if not isinstance(batch, list):
            raise ValueError("GitHub returned an invalid release list")
        releases.extend(batch)
        if len(batch) < RELEASES_PER_PAGE:
            break
    return releases


async def _latest_stable_release(session):
    # The official latest endpoint excludes prereleases and remains usable even
    # after hundreds of development builds have accumulated in the release list.
    async with session.get(LATEST_RELEASE_URL) as response:
        if response.status == 404:
            return None
        response.raise_for_status()
        release = await response.json()
    if (_installable(release) and release.get("prerelease") is False
            and _stable_version(release.get("tag_name")) is not None):
        return release
    return None


async def _read_build_info(session, release):
    tag = release["tag_name"]
    for asset in release["assets"]:
        if not isinstance(asset, dict) or asset.get("name") != "build-info.json":
            continue
        url = _asset_url(asset, tag)
        if not url:
            continue
        async with session.get(url) as response:
            response.raise_for_status()
            # Release assets use application/octet-stream, so decode JSON
            # explicitly and bound the response independently of Content-Length.
            payload = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                payload.extend(chunk)
                if len(payload) > MAX_METADATA_BYTES:
                    raise ValueError("Build metadata exceeds the allowed size")
        return _validate_build_info(json.loads(payload), tag)
    raise ValueError("Build metadata asset is missing")


def _current_actions_build(current_build, current_version):
    if not isinstance(current_build, dict) or current_build.get("channel") != "actions":
        return None
    try:
        tag = f"build-{_positive_int(current_build.get('run_id'))}-{_positive_int(current_build.get('run_attempt'))}"
        metadata = _validate_build_info(current_build, tag)
        if _base_version(metadata["version"]) != _base_version(current_version):
            return None
        return metadata
    except ValueError:
        return None


async def check_for_update(current_version, channel="release", current_build=None):
    """Check the selected public update channel without importing GUI code.

    ``current_build`` is the packaged build-info.json dictionary, if present.
    HTTP/timeout errors propagate to the UI; malformed individual releases are
    ignored. ``stable`` is accepted as an alias for the default ``release``.
    """
    channel = "release" if channel == "stable" else channel
    if channel not in ("release", "actions"):
        raise ValueError(f"Unknown update channel: {channel!r}")
    current_base = _base_version(current_version)
    current_actions = _current_actions_build(current_build, current_version)
    current_dev_version = _ACTIONS_VERSION.fullmatch(current_version)
    latest_release = None
    metadata = None
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "DG-LAB-VRCOSC-Updater"}
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT, trust_env=True, headers=headers) as session:
        if channel == "release":
            latest_release = await _latest_stable_release(session)
            if latest_release is not None:
                latest_base = _stable_version(latest_release["tag_name"])
            else:
                releases = await _list_releases(session)
                candidates = [(parsed, release) for release in releases
                              if _installable(release) and release.get("prerelease") is False
                              and (parsed := _stable_version(release.get("tag_name"))) is not None]
                if candidates:
                    latest_base, latest_release = max(candidates, key=lambda item: item[0])
        else:
            releases = await _list_releases(session)
            candidates = []
            for release in releases:
                if not _installable(release) or release.get("prerelease") is not True:
                    continue
                tag = release.get("tag_name")
                match = _BUILD_TAG.fullmatch(tag) if isinstance(tag, str) else None
                if match:
                    candidates.append((tuple(map(int, match.groups())), release))
            for _, release in sorted(candidates, key=lambda item: item[0], reverse=True):
                try:
                    metadata = await _read_build_info(session, release)
                except (ValueError, UnicodeError) as exc:
                    logger.warning("Ignoring invalid Actions release %s: %s", release["tag_name"], exc)
                    continue
                latest_release = release
                latest_base = _base_version(metadata["version"])
                break

    result = {"available": False, "current_version": current_version,
              "latest_version": None, "message": "未找到所选更新通道的可用更新包"}
    if latest_release is None:
        return result
    release_info = dict(latest_release, channel=channel)
    if metadata is not None:
        release_info["build_info"] = metadata
    result.update(release_info=release_info,
                  latest_version=metadata["version"] if metadata else latest_release["tag_name"],
                  message="当前已是所选更新通道的最新版本")
    if channel == "release":
        # Installing a stable release is valid when leaving an Actions build
        # with the same base version, even though its displayed version differs.
        available = latest_base > current_base or (latest_base == current_base and (current_actions is not None or current_dev_version is not None))
    elif latest_base < current_base:
        available = False
    elif current_actions is None:
        # The user explicitly opted into Actions, including same-base builds.
        available = True
        if latest_base == current_base and current_dev_version is not None:
            # A missing/damaged manifest should not make the same .dev build
            # appear as an update on every startup. Attempt ordering requires
            # the manifest, but the displayed version still identifies its run.
            available = metadata["run_number"] > int(current_dev_version.group(4))
    else:
        latest_run = (metadata["run_id"], metadata["run_attempt"])
        current_run = (current_actions["run_id"], current_actions["run_attempt"])
        available = latest_base >= current_base and latest_run > current_run
    result["available"] = available
    if available:
        result["message"] = "发现可用更新"
    return result
