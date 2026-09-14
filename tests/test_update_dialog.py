"""Verify update UI lifecycle without downloads, installs, or application exit."""
import asyncio
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from i18n import translate
from update_handler import UpdateDialog, UpdateHandler


def release_info(tag='v0.4.9'):
    return {
        'tag_name': tag,
        'body': 'Update notes',
        'channel': 'release',
        'assets': [{
            'name': 'DG-LAB-VRCOSC.zip',
            'browser_download_url': f'https://github.com/ccvrc/DG-LAB-VRCOSC/releases/download/{tag}/DG-LAB-VRCOSC.zip',
            'size': 200,
        }],
    }


class UpdateHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_current_channel_and_build_info_for_every_check(self):
        settings = {}
        handler = UpdateHandler('v0.4.8', settings)
        build_info = {'channel': 'actions', 'run_id': 123}
        response = {'available': True, 'release_info': release_info(), 'latest_version': 'v0.4.9'}
        with patch('update_handler.read_build_info', return_value=build_info), \
             patch('update_handler.check_for_update', new=AsyncMock(return_value=response)) as check:
            self.assertEqual(await handler.check_update(), response)
            check.assert_awaited_once_with('v0.4.8', channel='release', current_build=build_info)
            settings['update_channel'] = 'actions'
            await handler.check_update(manual_check=False)
            self.assertEqual(check.await_count, 2)
            self.assertEqual(check.await_args.kwargs['channel'], 'actions')
            self.assertEqual(check.await_args.kwargs['current_build'], build_info)

    async def test_manual_network_failure_is_reported_without_false_latest_message(self):
        handler = UpdateHandler('v0.4.8', {})
        with patch('update_handler.read_build_info', return_value=None), \
             patch('update_handler.check_for_update', new=AsyncMock(side_effect=RuntimeError('offline'))):
            result = await handler.check_update()
            self.assertFalse(result['available'])
            self.assertEqual(result['message'], translate('about_tab.update_failed').format(error='offline'))
            self.assertIsNone(await handler.check_update(manual_check=False))

    async def test_empty_channel_and_current_version_have_distinct_messages(self):
        handler = UpdateHandler('v0.4.9', {})
        cases = ((None, 'about_tab.no_release'), ('v0.4.9', 'about_tab.already_latest_version'))
        for latest_version, key in cases:
            with self.subTest(latest_version=latest_version):
                response = {'available': False, 'latest_version': latest_version}
                with patch('update_handler.read_build_info', return_value=None), \
                     patch('update_handler.check_for_update', new=AsyncMock(return_value=response)):
                    self.assertEqual((await handler.check_update())['message'], translate(key))


class UpdateDialogTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent_window = QWidget()
        self.parent_window.save_settings = Mock()
        self.addCleanup(self.parent_window.close)
        self.stage = Path('mock-update-work') / 'stage'
        self.prepare = self.start_patch('update_handler.prepare_update', new_callable=AsyncMock)
        self.prepare.return_value = self.stage
        self.launch = self.start_patch('update_handler.launch_installer')
        self.open_url = self.start_patch('update_handler.QDesktopServices.openUrl')
        self.remove = self.start_patch('update_handler.discard_update')
        self.app_class = self.start_patch('update_handler.QApplication')
        self.quit = self.app_class.instance.return_value.quit
        self.start_patch('update_handler.sys.frozen', True, create=True)

    def start_patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def create_dialog(self, release=None):
        dialog = UpdateDialog(self.parent_window, release_info() if release is None else release)
        self.addCleanup(dialog.reject)
        return dialog

    async def test_actions_display_uses_build_version(self):
        release = release_info('build-200-1')
        release.update(channel='actions', build_info={'version': 'v0.4.9.dev20'})
        dialog = self.create_dialog(release)
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertIn(f"{translate('about_tab.new_version')} v0.4.9.dev20", labels)
        self.assertFalse(any('build-200-1' in text for text in labels))

    async def test_source_mode_opens_official_release_without_downloading_or_installing(self):
        for tag in ('v0.4.9', 'build-200-1'):
            with self.subTest(tag=tag):
                release = release_info(tag)
                release['html_url'] = 'https://example.invalid/untrusted'
                dialog = self.create_dialog(release)
                with patch('update_handler.sys.frozen', False):
                    dialog.update_btn.click()
                self.assertEqual(
                    self.open_url.call_args.args[0].toString(),
                    f'https://github.com/ccvrc/DG-LAB-VRCOSC/releases/tag/{tag}',
                )
                self.assertEqual(dialog.status_label.text(), translate('about_tab.source_update_manual'))
                self.assertIsNone(dialog._download_task)
        self.prepare.assert_not_awaited()
        self.launch.assert_not_called()
        self.quit.assert_not_called()

    async def test_download_must_finish_before_restart_is_enabled(self):
        started = asyncio.Event()
        finish = asyncio.Event()

        async def prepare(asset, progress):
            progress(42)
            started.set()
            await finish.wait()
            return self.stage

        self.prepare.side_effect = prepare
        dialog = self.create_dialog()
        dialog.update_btn.click()
        await started.wait()
        self.assertFalse(dialog.update_btn.isEnabled())
        self.assertEqual(dialog.update_btn.text(), translate('about_tab.update_now'))
        self.assertEqual(dialog.status_label.text(), translate('about_tab.download_progress').format(percent=42))
        dialog.start_download()
        self.prepare.assert_awaited_once()
        self.launch.assert_not_called()
        self.quit.assert_not_called()

        finish.set()
        await dialog._download_task
        self.assertTrue(dialog.update_btn.isEnabled())
        self.assertEqual(dialog.update_btn.text(), translate('about_tab.restart_to_update'))
        self.assertEqual(dialog.status_label.text(), translate('about_tab.update_ready'))
        self.prepare.assert_awaited_once_with(dialog.release_info['assets'][0], dialog._progress)
        self.launch.assert_not_called()
        self.quit.assert_not_called()

        dialog.update_btn.click()
        self.parent_window.save_settings.assert_called_once_with()
        self.launch.assert_called_once_with(self.stage)
        self.quit.assert_called_once_with()
        dialog.reject()
        self.remove.assert_not_called()

    async def test_download_failure_keeps_application_open_and_allows_retry(self):
        self.prepare.side_effect = [RuntimeError('connection reset'), self.stage]
        dialog = self.create_dialog()
        dialog.update_btn.click()
        await dialog._download_task
        self.assertTrue(dialog.update_btn.isEnabled())
        self.assertEqual(dialog.update_btn.text(), translate('about_tab.update_now'))
        self.assertIn('connection reset', dialog.status_label.text())
        self.assertIsNone(dialog._stage)
        self.launch.assert_not_called()
        self.quit.assert_not_called()

        dialog.update_btn.click()
        await dialog._download_task
        self.assertEqual(self.prepare.await_count, 2)
        self.assertEqual(dialog.update_btn.text(), translate('about_tab.restart_to_update'))
        self.launch.assert_not_called()
        self.quit.assert_not_called()

    async def test_launch_failure_keeps_staged_update_and_application_open_for_retry(self):
        dialog = self.create_dialog()
        dialog.update_btn.click()
        await dialog._download_task
        self.launch.side_effect = [OSError('access denied'), Mock()]
        dialog.update_btn.click()
        self.assertIn('access denied', dialog.status_label.text())
        self.assertEqual(dialog._stage, self.stage)
        self.assertTrue(dialog.update_btn.isEnabled())
        self.quit.assert_not_called()
        self.remove.assert_not_called()

        dialog.update_btn.click()
        self.assertEqual(self.launch.call_count, 2)
        self.prepare.assert_awaited_once()
        self.quit.assert_called_once_with()

    async def test_cancel_discards_prepared_stage_without_installing(self):
        dialog = self.create_dialog()
        dialog.update_btn.click()
        await dialog._download_task
        dialog.cancel_btn.click()
        self.remove.assert_called_once_with(self.stage)
        self.assertIsNone(dialog._stage)
        self.launch.assert_not_called()
        self.quit.assert_not_called()

    async def test_cancel_stops_pending_download(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def prepare(asset, progress):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        self.prepare.side_effect = prepare
        dialog = self.create_dialog()
        dialog.update_btn.click()
        await started.wait()
        dialog.cancel_btn.click()
        await dialog._download_task
        self.assertTrue(cancelled.is_set())
        self.assertTrue(dialog._download_task.done())
        self.assertIsNone(dialog._stage)
        self.launch.assert_not_called()
        self.quit.assert_not_called()

    async def test_cancel_discards_a_stage_returned_during_cancellation(self):
        started = asyncio.Event()

        async def prepare(asset, progress):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return self.stage

        self.prepare.side_effect = prepare
        dialog = self.create_dialog()
        dialog.update_btn.click()
        await started.wait()
        before_close = dialog.status_label.text()
        dialog.cancel_btn.click()
        dialog._progress(100)
        await dialog._download_task
        self.assertEqual(dialog.status_label.text(), before_close)
        self.remove.assert_called_once_with(self.stage)
        self.assertIsNone(dialog._stage)
        self.launch.assert_not_called()
        self.quit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
