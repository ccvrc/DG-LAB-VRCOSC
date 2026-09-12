"""Update channel and provenance checks, with no network or executable installs."""

import copy
import json
import unittest
from unittest.mock import patch

import aiohttp

from services import github_updates as updates


def asset(tag, name):
    return {"name": name, "browser_download_url": f"https://github.com/{updates.REPOSITORY}/releases/download/{tag}/{name}"}


def release(tag="v0.4.9", prerelease=False, draft=False):
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft,
            "body": "Changes", "assets": [asset(tag, f"DG-LAB-VRCOSC-{tag}.zip")]}


def build(run_id=200, run_number=20, run_attempt=1, base="v0.4.9"):
    tag = f"build-{run_id}-{run_attempt}"
    info = {"channel": "actions", "repository": updates.REPOSITORY,
            "workflow": updates.WORKFLOW, "branch": "master", "commit": "a" * 40,
            "version": f"{base}.dev{run_number}", "run_id": run_id,
            "run_number": run_number, "run_attempt": run_attempt}
    item = release(tag, prerelease=True)
    item["assets"].append(asset(tag, "build-info.json"))
    return item, info


class FakeContent:
    def __init__(self, data):
        self.data = data

    async def iter_chunked(self, size):
        for offset in range(0, len(self.data), size):
            yield self.data[offset:offset + size]


class FakeResponse:
    def __init__(self, value, error=None, raw=None, status=200):
        self.value = value
        self.error = error
        self.status = status
        self.content = FakeContent(raw if raw is not None else json.dumps(value).encode("utf-8"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def raise_for_status(self):
        if self.error:
            raise self.error

    async def json(self):
        return self.value


class FakeSession:
    def __init__(self, pages=None, metadata=None, latest=None):
        self.pages = pages if pages is not None else [[]]
        self.metadata = metadata or {}
        self.latest = latest if latest is not None else FakeResponse(None, status=404)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if url == updates.LATEST_RELEASE_URL:
            value = self.latest
        elif url == updates.RELEASES_URL:
            value = self.pages[kwargs["params"]["page"] - 1]
        else:
            value = self.metadata[url]
        if isinstance(value, FakeResponse):
            return value
        return FakeResponse(value)


class GitHubUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, releases, current="v0.4.8", channel="release", current_build=None, metadata=None, pages=None, latest=None):
        session = FakeSession(pages if pages is not None else [releases], metadata, latest)
        with patch.object(updates.aiohttp, "ClientSession", return_value=session) as factory:
            result = await updates.check_for_update(current, channel, current_build)
        self.assertTrue(factory.call_args.kwargs["trust_env"])
        self.assertEqual(factory.call_args.kwargs["timeout"].total, 30)
        return result, session

    async def test_default_release_uses_official_latest_without_loading_all_builds(self):
        result, session = await self.check([], latest=release())
        self.assertTrue(result["available"])
        self.assertEqual(session.requests, [(updates.LATEST_RELEASE_URL, {})])

    async def test_uninstallable_latest_falls_back_to_release_pages(self):
        newest = release("v0.4.10")
        newest["assets"] = []
        result, session = await self.check([release()], latest=newest)
        self.assertEqual(result["latest_version"], "v0.4.9")
        self.assertEqual(len(session.requests), 2)

    async def test_default_release_selects_highest_semver_and_ignores_uninstallable_tags(self):
        invalid = [release("preview"), release("v9.0.0", draft=True),
                   release("v8.0.0", prerelease=True), release("v7.0.0-rc1"),
                   release("v6.0.0"), release("v01.0.0")]
        invalid[-2]["assets"] = []
        result, _ = await self.check(invalid + [release("v0.4.9"), release("v0.4.10")])
        self.assertTrue(result["available"])
        self.assertEqual(result["latest_version"], "v0.4.10")
        self.assertEqual(result["release_info"]["channel"], "release")

    async def test_stable_alias_and_historical_date_suffix_are_supported(self):
        result, _ = await self.check([release()], current="v0.4.8-20250806-1827-8f0b6c8", channel="stable")
        self.assertTrue(result["available"])

    async def test_release_does_not_downgrade_or_reoffer_current_version(self):
        for tag in ("v0.4.8", "v0.4.9", "v0.4.9+other-build"):
            with self.subTest(tag=tag):
                result, _ = await self.check([release(tag)], current="v0.4.9")
                self.assertFalse(result["available"])

    async def test_empty_or_invalid_releases_return_no_update(self):
        for releases in ([], [None, {}, release("not-a-version")]):
            with self.subTest(releases=releases):
                result, _ = await self.check(releases)
                self.assertFalse(result["available"])
                self.assertIsNone(result["latest_version"])

    async def test_pagination_finds_stable_after_prereleases(self):
        first = [release(f"ignored-{i}", prerelease=True) for i in range(updates.RELEASES_PER_PAGE)]
        result, session = await self.check([], pages=[first, [release()]])
        self.assertTrue(result["available"])
        self.assertEqual([kwargs["params"]["page"] for url, kwargs in session.requests if url == updates.RELEASES_URL], [1, 2])

    async def test_pagination_is_bounded(self):
        page = [release("ignored", prerelease=True)] * updates.RELEASES_PER_PAGE
        _, session = await self.check([], pages=[page] * updates.MAX_RELEASE_PAGES)
        self.assertEqual(len(session.requests), updates.MAX_RELEASE_PAGES + 1)

    async def test_latest_actions_build_uses_numeric_run_order(self):
        old, _ = build(run_id=99)
        latest, info = build(run_id=100)
        url = latest["assets"][1]["browser_download_url"]
        result, session = await self.check([old, latest], current="v0.4.9", channel="actions", metadata={url: info})
        self.assertTrue(result["available"])
        self.assertEqual(result["latest_version"], "v0.4.9.dev20")
        self.assertEqual(result["release_info"]["build_info"], info)
        self.assertEqual(len(session.requests), 2)

    async def test_current_actions_build_is_not_reoffered_or_downgraded(self):
        _, current = build(run_id=200, run_attempt=2)
        for run_id, run_attempt, expected in ((199, 3, False), (200, 1, False),
                                             (200, 2, False), (200, 3, True), (201, 1, True)):
            with self.subTest(run_id=run_id, run_attempt=run_attempt):
                candidate, info = build(run_id=run_id, run_attempt=run_attempt)
                result, _ = await self.check([candidate], current=current["version"], channel="actions", current_build=current,
                                            metadata={candidate["assets"][1]["browser_download_url"]: info})
                self.assertEqual(result["available"], expected)

    async def test_old_base_actions_build_cannot_replace_newer_release_or_actions(self):
        candidate, info = build(base="v0.4.8", run_id=999)
        _, installed = build(base="v0.4.9", run_id=100)
        for current, current_build in (("v0.4.9", None), (installed["version"], installed)):
            with self.subTest(current=current):
                result, _ = await self.check([candidate], current=current, channel="actions", current_build=current_build,
                                            metadata={candidate["assets"][1]["browser_download_url"]: info})
                self.assertFalse(result["available"])

    async def test_actions_can_switch_back_to_same_base_stable(self):
        _, installed = build()
        result, _ = await self.check([release()], current=installed["version"], current_build=installed)
        self.assertTrue(result["available"])
        result, _ = await self.check([release("v0.4.8")], current=installed["version"], current_build=installed)
        self.assertFalse(result["available"])

    async def test_missing_manifest_still_avoids_reoffering_the_same_dev_run(self):
        candidate, info = build()
        url = candidate["assets"][1]["browser_download_url"]
        for current, expected in (("v0.4.9.dev19", True), ("v0.4.9.dev20", False), ("v0.4.9.dev21", False)):
            with self.subTest(current=current):
                result, _ = await self.check([candidate], current=current, channel="actions", metadata={url: info})
                self.assertEqual(result["available"], expected)
        result, _ = await self.check([], current="v0.4.9.dev20", latest=release())
        self.assertTrue(result["available"])

    async def test_actions_metadata_run_fields_can_be_numeric_strings(self):
        candidate, info = build()
        info.update(run_id="200", run_number="20", run_attempt="1")
        result, _ = await self.check([candidate], channel="actions", metadata={candidate["assets"][1]["browser_download_url"]: info})
        self.assertTrue(result["available"])
        self.assertEqual(result["release_info"]["build_info"]["run_id"], 200)

    async def test_untrusted_or_inconsistent_build_metadata_is_skipped(self):
        candidate, info = build()
        cases = {"channel": "stable", "repository": "fork/DG-LAB-VRCOSC", "workflow": "untrusted.yml",
                 "branch": "feature", "commit": "bad", "version": "v0.4.9.dev21", "run_id": 201,
                 "run_attempt": 2, "run_number": True}
        for key, value in cases.items():
            with self.subTest(key=key):
                malformed = dict(info, **{key: value})
                result, _ = await self.check([candidate], channel="actions", metadata={candidate["assets"][1]["browser_download_url"]: malformed})
                self.assertFalse(result["available"])

    async def test_missing_or_malformed_metadata_skips_to_previous_valid_build(self):
        latest, _ = build(run_id=201)
        older, info = build(run_id=200)
        old_url = older["assets"][1]["browser_download_url"]
        bad_url = latest["assets"][1]["browser_download_url"]
        for metadata in ({bad_url: FakeResponse(None, raw=b"not json"), old_url: info},
                         {bad_url: FakeResponse(None, raw=b"x" * (updates.MAX_METADATA_BYTES + 1)), old_url: info}):
            result, _ = await self.check([latest, older], channel="actions", metadata=metadata)
            self.assertEqual(result["release_info"]["tag_name"], older["tag_name"])
        latest["assets"].pop()
        result, _ = await self.check([latest, older], channel="actions", metadata={old_url: info})
        self.assertEqual(result["release_info"]["tag_name"], older["tag_name"])

    async def test_actions_ignores_other_prerelease_formats_and_stable_releases(self):
        result, session = await self.check([release(), release("actions-latest", prerelease=True),
                                           release("build-200-1", prerelease=False)], channel="actions")
        self.assertFalse(result["available"])
        self.assertEqual(len(session.requests), 1)

    async def test_http_errors_propagate_instead_of_reporting_already_current(self):
        session = FakeSession([FakeResponse(None, error=aiohttp.ClientConnectionError("offline"))])
        with patch.object(updates.aiohttp, "ClientSession", return_value=session):
            with self.assertRaises(aiohttp.ClientConnectionError):
                await updates.check_for_update("v0.4.9")

    async def test_invalid_api_response_is_reported(self):
        with self.assertRaisesRegex(ValueError, "invalid release list"):
            await self.check([], pages=[{"message": "unexpected"}])

    async def test_invalid_config_is_rejected_before_network_access(self):
        with patch.object(updates.aiohttp, "ClientSession") as factory:
            with self.assertRaises(ValueError):
                await updates.check_for_update("v0.4.9", "mirror")
            with self.assertRaises(ValueError):
                await updates.check_for_update("invalid")
            factory.assert_not_called()


class DownloadUrlTests(unittest.TestCase):
    def test_legacy_and_versioned_official_assets_are_supported(self):
        for name in ("DG-LAB-VRCOSC.zip", "DG-LAB-VRCOSC-v0.4.9.zip"):
            item = release()
            item["assets"] = [asset(item["tag_name"], name)]
            self.assertEqual(updates.get_download_url(item), item["assets"][0]["browser_download_url"])

    def test_mirrors_other_repositories_and_unrelated_files_are_rejected(self):
        item = release()
        good = item["assets"][0]["browser_download_url"]
        for url in (good.replace("github.com", "github.aqa.moe"), good.replace("ccvrc/", "other/"),
                    good.replace("https:", "http:"), good + "?redirect=bad", good.replace("v0.4.9/", "v0.4.8/")):
            with self.subTest(url=url):
                bad = copy.deepcopy(item)
                bad["assets"][0]["browser_download_url"] = url
                with self.assertRaises(ValueError):
                    updates.get_download_url(bad)
        item["assets"] = [asset("v0.4.9", "DG-LAB-VRCOSCEvil.zip")]
        with self.assertRaises(ValueError):
            updates.get_download_url(item)


if __name__ == "__main__":
    unittest.main()
