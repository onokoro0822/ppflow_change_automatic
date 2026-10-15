"""Run the saved calibration and report serially, with a single-worker lock.

Launch detached under caffeinate; pipeline_status.json records completion/errors.
Never deletes or overwrites incomplete activity outputs.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.local/pflow/output/independent_A_calibration_20261015'
DOCUMENT=ROOT/'docs/progress/20261015_新施設専用重み校正.md'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'pipeline.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        original=hashlib.sha256(DOCUMENT.read_bytes()).hexdigest()
        def state(stage,**extra):
            value=dict(stage=stage,pid=os.getpid(),updated_utc=datetime.now(timezone.utc).isoformat(),**extra)
            temp=OUT/'pipeline_status.tmp';temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(OUT/'pipeline_status.json')
            print(json.dumps(value),flush=True)
        try:
            state('calibrating')
            subprocess.run([sys.executable,str(ROOT/'pflow_independent_calibration.py'),'--output',str(OUT),'--resume'],cwd=ROOT,check=True)
            if hashlib.sha256(DOCUMENT.read_bytes()).hexdigest()!=original:
                state('report_pending_document_changed');return
            state('reporting')
            subprocess.run([sys.executable,str(ROOT/'scripts/report_independent_a.py')],cwd=ROOT,check=True)
            state('results_ready_for_review')
        except BaseException as exc:
            state('failed',error=str(exc));raise
if __name__=='__main__':main()
