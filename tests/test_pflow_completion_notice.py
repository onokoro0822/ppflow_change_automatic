import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from scripts import pflow_completion_notice as n

class CompletionNoticeTest(unittest.TestCase):
    def test_incomplete_is_not_success(self):
        self.assertIn('停止',n.notice_text({'stage':'calibrating'})[0])
        self.assertIn('確認',n.notice_text({'stage':'report_pending_document_changed'})[0])
    def test_duplicate_exit_and_hook_notify_only_once(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'pipeline_status.json').write_text(json.dumps(dict(stage='results_ready_for_review',pid=123,updated_utc='t')))
            with patch.object(n.subprocess,'run',return_value=Mock(returncode=0,stderr='')) as banner,patch.object(n.subprocess,'Popen',return_value=Mock(pid=321)) as dialog:
                n.notify(p);n.notify(p,expected_pid=123,exited=True)
                self.assertEqual(banner.call_count,1);self.assertEqual(dialog.call_count,1)
                self.assertIn('完了',(p/'終了結果.txt').read_text())
    def test_banner_timeout_still_opens_dialog(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'pipeline_status.json').write_text(json.dumps(dict(stage='failed',pid=123)))
            with patch.object(n.subprocess,'run',side_effect=subprocess.TimeoutExpired('osascript',20)),patch.object(n.subprocess,'Popen',return_value=Mock(pid=321)) as dialog:
                n.notify(p);dialog.assert_called_once()
    @unittest.skipUnless(hasattr(n.select,'kqueue'),'macOS/BSD event API')
    def test_waits_for_process_exit_without_polling(self):
        with tempfile.TemporaryDirectory() as temp:
            child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.2)'])
            try:
                with patch.object(n,'notify') as notice:
                    n.wait_and_notify(temp,child.pid)
                    notice.assert_called_once_with(temp,expected_pid=child.pid,exited=True)
                self.assertEqual(child.wait(timeout=2),0)
            finally:
                if child.poll() is None:child.kill();child.wait()
