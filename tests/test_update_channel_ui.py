"""Exercise update-channel settings and UI without making network requests."""
import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from config import DEFAULT_SETTINGS, load_settings, save_settings
from gui.about_tab import AboutTab
from i18n import get_current_language, set_language, translate


class UpdateChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def create_tab(self, settings=None):
        window = SimpleNamespace(
            settings=dict(DEFAULT_SETTINGS if settings is None else settings),
            update_handler=SimpleNamespace(current_version='0.4.9'),
            save_settings=Mock(),
            check_update_manual=AsyncMock(),
        )
        tab = AboutTab(window)
        self.addCleanup(tab.close)
        return tab

    def test_missing_settings_and_old_settings_default_to_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.yml'
            with patch('config.get_config_file_path', return_value=str(path)):
                self.assertEqual(load_settings()['update_channel'], 'release')
                path.write_text('auto_update: true\n', encoding='utf-8')
                settings = load_settings()
        self.assertEqual(settings['update_channel'], 'release')
        tab = self.create_tab(settings)
        self.assertEqual(tab.update_channel_combo.currentData(), 'release')
        self.assertTrue(tab.update_channel_hint.isHidden())
        tab.main_window.save_settings.assert_not_called()

    def test_invalid_channel_falls_back_to_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.yml'
            with patch('config.get_config_file_path', return_value=str(path)):
                for invalid in ('mirror', 'null', '123', '[actions]'):
                    with self.subTest(value=invalid):
                        path.write_text(f'update_channel: {invalid}\n', encoding='utf-8')
                        self.assertEqual(load_settings()['update_channel'], 'release')

    def test_selection_is_saved_and_restored(self):
        tab = self.create_tab()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.yml'
            with patch('config.get_config_file_path', return_value=str(path)):
                tab.main_window.save_settings.side_effect = lambda: save_settings(tab.main_window.settings)
                tab.update_channel_combo.setCurrentIndex(tab.update_channel_combo.findData('actions'))
                tab.main_window.save_settings.assert_called_once_with()
                self.assertEqual(tab.main_window.settings['update_channel'], 'actions')
                self.assertFalse(tab.update_channel_hint.isHidden())
                restored = self.create_tab(load_settings())
                self.assertEqual(restored.update_channel_combo.currentData(), 'actions')
                self.assertFalse(restored.update_channel_hint.isHidden())

                tab.update_channel_combo.setCurrentIndex(tab.update_channel_combo.findData('release'))
                self.assertEqual(load_settings()['update_channel'], 'release')
                self.assertTrue(tab.update_channel_hint.isHidden())

    def test_language_change_preserves_channel_without_saving(self):
        original_language = get_current_language()
        self.addCleanup(set_language, original_language)
        tab = self.create_tab({**DEFAULT_SETTINGS, 'update_channel': 'actions'})
        for language in ('en', 'ja', 'zh'):
            with self.subTest(language=language):
                set_language(language)
                tab.update_ui_texts()
                self.assertEqual(tab.update_channel_combo.currentData(), 'actions')
                self.assertEqual(tab.main_window.settings['update_channel'], 'actions')
                self.assertEqual(tab.update_channel_combo.currentText(), translate('about_tab.actions_channel'))
                self.assertEqual(tab.update_channel_label.text(), translate('about_tab.update_channel'))
                self.assertEqual(tab.update_channel_hint.text(), translate('about_tab.actions_channel_hint'))
                self.assertFalse(tab.update_channel_hint.isHidden())
        tab.main_window.save_settings.assert_not_called()

    def test_check_uses_main_window_with_selected_channel_and_blocks_duplicate_clicks(self):
        tab = self.create_tab()

        async def scenario():
            started = asyncio.Event()
            finish = asyncio.Event()
            observed_channels = []

            async def check_update():
                observed_channels.append(tab.main_window.settings['update_channel'])
                started.set()
                await finish.wait()

            tab.main_window.check_update_manual.side_effect = check_update
            tab.update_channel_combo.setCurrentIndex(tab.update_channel_combo.findData('actions'))
            tab.check_update()
            await started.wait()
            self.assertFalse(tab.check_update_btn.isEnabled())
            tab.check_update()
            self.assertEqual(observed_channels, ['actions'])
            tab.main_window.check_update_manual.assert_awaited_once_with()
            finish.set()
            await asyncio.sleep(0)
            self.assertTrue(tab.check_update_btn.isEnabled())

        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
