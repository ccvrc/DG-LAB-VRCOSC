import logging
import os
from pathlib import Path
import sys
import threading
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QTextEdit

from gui.log_viewer_tab import QTextEditHandler, SimpleFormatter


class SimpleFormatterTests(unittest.TestCase):
    def test_formats_fresh_record_without_another_handler(self):
        record = logging.LogRecord('test', logging.WARNING, __file__, 1, 'value %s', ('ok',), None)
        formatter = SimpleFormatter('%(asctime)s-%(levelname)s: %(message)s', datefmt='%H:%M:%S')

        message = formatter.format(record)

        self.assertEqual(message, f'{formatter.formatTime(record, "%H:%M:%S")}-W: value ok')
        self.assertEqual(record.levelname, 'WARNING')

    def test_preserves_exception_traceback(self):
        try:
            raise ValueError('failure detail')
        except ValueError:
            record = logging.LogRecord('test', logging.ERROR, __file__, 1, 'failed', (), sys.exc_info())
        formatter = SimpleFormatter('%(levelname)s: %(message)s')

        message = formatter.format(record)

        self.assertIn('E: failed', message)
        self.assertIn('ValueError: failure detail', message)


class RecordingTextEdit(QTextEdit):
    def __init__(self):
        super().__init__()
        self.append_threads = []

    def append(self, text):
        self.append_threads.append(QThread.currentThread())
        super().append(text)


class QTextEditHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = RecordingTextEdit()
        self.handler = QTextEditHandler(self.widget)

    def tearDown(self):
        self.handler.close()
        self.widget.close()

    def test_background_logging_updates_widget_on_gui_thread_and_preserves_text(self):
        message = 'device <unknown> & value\nnext line'
        record = logging.LogRecord('test', logging.ERROR, __file__, 1, message, (), None)
        worker = threading.Thread(target=self.handler.handle, args=(record,))
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.widget.toPlainText(), '')

        self.app.processEvents()

        self.assertEqual(self.widget.toPlainText(), message)
        self.assertEqual(self.widget.append_threads, [self.app.thread()])


if __name__ == '__main__':
    unittest.main()
