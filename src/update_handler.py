"""GitHub update channels and the download/install dialog."""
import asyncio
import logging
import sys
from urllib.parse import quote

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QPlainTextEdit,
)

from i18n import translate as _
from services.github_updates import check_for_update, get_download_url
from services.update_installer import read_build_info, prepare_update, launch_installer, discard_update

logger = logging.getLogger(__name__)


class UpdateHandler:
    def __init__(self, current_version, config):
        self.current_version = current_version
        self.config = config

    async def check_update(self, manual_check=True):
        try:
            result = await check_for_update(
                self.current_version,
                channel=self.config.get('update_channel', 'release'),
                current_build=read_build_info(),
            )
            if not result['available']:
                key = 'about_tab.no_release' if result.get('latest_version') is None else 'about_tab.already_latest_version'
                result['message'] = str(_(key))
            return result
        except Exception as exc:
            logger.error('GitHub update check failed: %s', exc)
            if manual_check:
                return {'available': False, 'message': str(_('about_tab.update_failed')).format(error=exc)}
            return None


class UpdateDialog(QDialog):
    def __init__(self, parent, release_info):
        super().__init__(parent)
        self.release_info = release_info
        self._download_task = None
        self._stage = None
        self._closing = False
        self._installing = False
        self.setWindowTitle(_('about_tab.discover_new_version'))
        self.resize(620, 460)
        layout = QVBoxLayout(self)
        display_version = release_info.get('build_info', {}).get('version', release_info['tag_name'])
        label = QLabel(f"{_('about_tab.new_version')} {display_version}")
        layout.addWidget(label)
        notes = QPlainTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(release_info.get('body') or '')
        layout.addWidget(notes)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QHBoxLayout()
        self.update_btn = QPushButton(_('about_tab.update_now'))
        self.cancel_btn = QPushButton(_('about_tab.cancel'))
        buttons.addWidget(self.update_btn)
        buttons.addWidget(self.cancel_btn)
        layout.addLayout(buttons)
        self.update_btn.clicked.connect(self.start_download)
        self.cancel_btn.clicked.connect(self.reject)

    def start_download(self):
        if self._download_task and not self._download_task.done():
            return
        if not getattr(sys, 'frozen', False):
            self.status_label.setText(_('about_tab.source_update_manual'))
            tag = quote(self.release_info['tag_name'], safe='')
            QDesktopServices.openUrl(QUrl(f'https://github.com/ccvrc/DG-LAB-VRCOSC/releases/tag/{tag}'))
            return
        if self._stage is not None:
            self.install_update()
            return
        self.update_btn.setEnabled(False)
        self.status_label.setText(_('about_tab.downloading'))
        self._download_task = asyncio.create_task(self._download())

    def _progress(self, percent):
        if not self._closing:
            self.status_label.setText(str(_('about_tab.download_progress')).format(percent=percent))

    async def _download(self):
        try:
            url = get_download_url(self.release_info)
            asset = next(a for a in self.release_info['assets'] if a.get('browser_download_url') == url)
            self._stage = await prepare_update(asset, self._progress)
            if not self._closing:
                self.status_label.setText(_('about_tab.update_ready'))
                self.update_btn.setText(_('about_tab.restart_to_update'))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error('Update download failed: %s', exc)
            if not self._closing:
                self.status_label.setText(str(_('about_tab.update_failed')).format(error=exc))
        finally:
            if self._closing:
                self._discard_stage()
            else:
                self.update_btn.setEnabled(True)

    def install_update(self):
        try:
            if self.parent() and hasattr(self.parent(), 'save_settings'):
                self.parent().save_settings()
            launch_installer(self._stage)
        except Exception as exc:
            self.status_label.setText(str(_('about_tab.update_failed')).format(error=exc))
            logger.error('Unable to launch update installer: %s', exc)
            return
        self._installing = True
        QApplication.instance().quit()

    def _discard_stage(self):
        if self._stage is not None and not self._installing:
            discard_update(self._stage)
            self._stage = None

    def reject(self):
        self._closing = True
        if self._download_task and not self._download_task.done():
            self._download_task.cancel()
        self._discard_stage()
        super().reject()
