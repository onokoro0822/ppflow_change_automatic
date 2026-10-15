"""Read Java task status counters, distinct from fatal exceptions and paired changes."""
import json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    report=json.loads((ROOT/'.local/pflow/output/gamma_A_52365790_20261014/report.json').read_text())
    controls=json.loads((ROOT/'.local/pflow/output/gamma_A_controls_20261015/controls.json').read_text())
    output={}
    for goal,record in report['targets'].items():
        output[goal]={}
        for state,metrics in [('closed',controls[goal]['postclosure_f1']),('new',record['best'])]:
            logs=Path(metrics['directory'])/'logs';counts={}
            for cohort in ['Commuter','NonCommuter','Student']:
                path=logs/(cohort+'.log');text=path.read_text()
                pattern=r'^\[\d+\] error:(\d+) total:(\d+)$' if cohort=='Student' else r'^\[\d+\]-(\d+)-(\d+)$'
                records=re.findall(pattern,text,re.M)
                if not records:raise ValueError('Missing status records: '+str(path))
                counts[cohort]=dict(nonzero_return_persons=sum(int(a) for a,b in records),processed_persons=sum(int(b) for a,b in records),source=str(path),fatal_marker_present=any(m in text for m in ['[ERROR]','Exception','Unresolved compilation']))
            output[goal][state]=counts
    dest=ROOT/'docs/progress/figures/20261015_gamma_A/generation_status.json';dest.write_text(json.dumps(output,indent=2)+'\n')
    for goal,v in output.items():
        for state,cohorts in v.items():print(goal,state,{c:r['nonzero_return_persons'] for c,r in cohorts.items()})
if __name__=='__main__':main()
