"""Verify saved final inputs and remeasure the full outputs before publication."""
import hashlib,json,sys,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pflow_scenario_metrics import references,measure
from pflow_gamma_optimize import check_generation_logs
from pflow_target import AnalysisTarget

def main():
    out=ROOT/'.local/pflow/output/independent_A_calibration_20261015'
    report=json.loads((out/'report.json').read_text())
    if report['status']!='finished':raise ValueError('Calibration is not finished')
    manifest=json.loads((out/'code_manifest.json').read_text())
    for name,digest in manifest['sha256'].items():
        if hashlib.sha256((ROOT.parent/name).read_bytes()).hexdigest()!=digest:raise ValueError('Source changed: '+name)
    units,origins,destinations=references(ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_d5_shopping_facility_age.csv',ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_purpose_mode_raw.csv')
    target=AnalysisTarget.load(ROOT/'config/gamma/analysis_target.json');audit={}
    for goal,record in report['targets'].items():
        old=record['best'];directory=Path(old['directory'])
        if not (directory/'.complete').exists() or len(list((directory/'23').glob('*.csv')))!=207:raise ValueError('Incomplete output: '+str(directory))
        if sorted(row['input']['seed'] for row in record['final_seeds'])!=[42,43,44,45]:raise ValueError('Incomplete final seeds')
        for row in record['final_seeds']:
            if row['input']['weight']!=old['input']['weight']:raise ValueError('Final seed weight mismatch')
        check_generation_logs(directory)
        fresh=measure(directory,target,units,origins,destinations)
        for key in ['counts','origin_counts','destination_counts','mesh_counts','city_counts']:
            if fresh[key]!=old[key]:raise ValueError('Remeasurement differs: '+goal+' '+key)
        for key in ['origin_tvd','destination_tvd']:
            if not math.isclose(fresh[key],old[key],rel_tol=0,abs_tol=1e-12):raise ValueError('TVD differs: '+goal+' '+key)
        audit[goal]=fresh
        print(goal,'full output matches saved metrics',fresh['counts'],flush=True)
    path=ROOT/'docs/progress/figures/20261015_independent_A/full_reaudit.json'
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
