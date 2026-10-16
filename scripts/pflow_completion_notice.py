"""One-shot macOS completion notification; optional process-exit event wait."""
import argparse
from contextlib import closing
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
from datetime import datetime, timezone

TERMINAL={'results_ready_for_review','failed','report_pending_document_changed'}

def notice_text(state):
    stage=state.get('stage')
    if stage=='results_ready_for_review':
        return 'PFLOW：計算・検証が完了', '比較表と図の生成が完了しました。Codexで「NEXT.mdから結果を確認して」と伝えてください。'
    if stage=='report_pending_document_changed':
        return 'PFLOW：結果の確認が必要', '計算は終了しました。文書の変更を検出したため、レポート更新を保留しています。'
    return 'PFLOW：処理が停止しました', '処理が正常完了しませんでした。NEXT.mdに記載したログを確認してください。'

def notify(out, expected_pid=None, exited=False):
    out=Path(out)
    with (out/'notification.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state=json.loads((out/'pipeline_status.json').read_text())
        if expected_pid is not None and state.get('pid')!=expected_pid:
            raise RuntimeError('Pipeline PID changed; refusing notice for another run')
        if state.get('stage') not in TERMINAL and not exited:
            raise ValueError('Pipeline has not finished')
        title,message=notice_text(state)
        run_key=hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()[:20]
        receipt=out/f'notification_{run_key}.json'
        if receipt.exists():return
        report=out/'終了結果.txt'
        report.write_text(title+'\n'+message+'\n\n'+json.dumps(state,ensure_ascii=False,indent=2)+'\n')
        # Arguments are passed to AppleScript as argv; no interpolated executable text.
        script='''on run argv
  display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"
end run'''
        try:
            result=subprocess.run(['/usr/bin/osascript','-e',script,title,message],capture_output=True,text=True,timeout=20)
            returncode,error=result.returncode,result.stderr
        except (OSError,subprocess.TimeoutExpired) as exc:
            returncode,error=None,str(exc)
        dialog='''on run argv
  display dialog (item 2 of argv) with title (item 1 of argv) buttons {"OK"} default button "OK" with icon note
end run'''
        with (out/'notification_dialog.log').open('a') as log:
            child=subprocess.Popen(['/usr/bin/osascript','-e',dialog,title,message],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        receipt.write_text(json.dumps(dict(state=state,notified_utc=datetime.now(timezone.utc).isoformat(),notification_returncode=returncode,notification_error=error,dialog_pid=child.pid),ensure_ascii=False,indent=2)+'\n')

def wait_and_notify(out,pid):
    # kqueue blocks in the kernel until exit: no timer, log polling or AI calls.
    with closing(select.kqueue()) as queue:
        event=select.kevent(pid,filter=select.KQ_FILTER_PROC,flags=select.KQ_EV_ADD|select.KQ_EV_ONESHOT,fflags=select.KQ_NOTE_EXIT)
        try:
            queue.control([event],0,0)
        except OSError as error:
            if error.errno!=errno.ESRCH:raise
        else:
            (Path(out)/'notification_watcher.json').write_text(json.dumps(dict(watcher_pid=os.getpid(),target_pid=pid,mode='kqueue NOTE_EXIT'),indent=2)+'\n')
            queue.control(None,1,None)
        notify(out,expected_pid=pid,exited=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--wait-pid',type=int);args=parser.parse_args()
    if args.wait_pid is None:notify(args.output)
    else:wait_and_notify(args.output,args.wait_pid)
if __name__=='__main__':main()
