#!/usr/bin/env python3
"""Verify / refine full-population target counts without AI or destination editing.

Reuse completed first full runs, then adjust only facility capacity to meet a
count tolerance. Commercial increments and MNL stay at pilot-selected values.
PT fit is reported separately; matching count is never labelled joint success.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import time

import chukyo_pt_origin_distribution as pt
import pflow_capacity_analysis as ca
import pflow_origin_comparison as oc
from pflow_joint_target_calibration import ARGS,atomic_json,next_capacity,choose_best

PROJECT=Path(__file__).resolve().parent


def refine_target(initial, evaluate, count_tolerance=.02, max_rounds=2, competing_capacity=3220000):
    history=[initial]
    target=initial['target']
    for iteration in range(max_rounds):
        if abs(history[-1]['arrivals_sample']-target)/target<=count_tolerance:
            break
        capacity=next_capacity(history,target,competing_capacity)
        if capacity==history[-1]['capacity']:
            break
        history.append(evaluate(capacity,iteration))
    return history


def main():
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--initial-full-report',type=Path)
    source.add_argument('--pilot-report',type=Path)
    p.add_argument('--baseline',type=Path,default=Path('/Volumes/PFLOW_SSD/PFLOW/output/fullsample_20261014/baseline'))
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--run-output-root',type=Path,required=True)
    p.add_argument('--pflow-home',type=Path,default=Path('/Volumes/PFLOW_SSD/PFLOW'))
    p.add_argument('--count-tolerance',type=float,default=.02)
    p.add_argument('--max-rounds',type=int,default=2)
    p.add_argument('--wait',action='store_true')
    args=p.parse_args()
    source_report=args.initial_full_report or args.pilot_report
    while True:
        initial=json.loads(source_report.read_text())
        if initial['status']=='failed':raise RuntimeError(initial.get('error','Initial full runs failed'))
        if initial['status']=='finished':break
        if not args.wait:raise RuntimeError('Initial full runs are not finished')
        time.sleep(3)
    if args.output.exists():raise ValueError('Output exists; choose a new directory')
    out=args.output.resolve();out.mkdir(parents=True);(out/'scenarios').mkdir()
    if args.run_output_root.exists():raise ValueError('Run output root already exists')
    labels=pt.load_middle_zone_labels(pt.DEFAULT_ZONE_CODE_XLSX);units=oc.ComparisonUnits(labels)
    report={k:initial[k] for k in ('seed','pt_raw_counts','pt_aichi_class_shares','pt_aichi_normalized','targets','mesh_additions')}
    report['baseline']=initial.get('baseline',str(args.baseline.resolve()))
    report.update(status='running',sample_factor=1,trials=[],best={},count_tolerance=args.count_tolerance,
                  distribution_tolerance=.15,method='full_count_refinement_with_fixed_commercial_increment',
                  initial_report=str(source_report.resolve()),max_refinement_rounds=args.max_rounds)
    atomic_json(out/'report.json',report)
    for target,first in initial['best'].items():
        def evaluate(capacity,iteration):
            scenario=json.loads(Path(first['scenario']).read_text())
            name=f't{target}_full_refine{iteration}_f{capacity:.0f}'
            scenario['scenarioId']=name;scenario['facility']['capacity']=capacity
            sp=out/'scenarios'/f'{name}.json';atomic_json(sp,scenario)
            cmd=['bash',str(PROJECT/'scripts/run_pflow_capacity_sweep.sh'),'--scenario-file',str(sp),
                 '--pflow-home',str(args.pflow_home.resolve()),'--output-root',str(args.run_output_root.resolve()),
                 '--seed',str(initial['seed']),'--sample-factor','1','--skip-compile','--pflow-args',ARGS]
            env=dict(os.environ);env['MAVEN_OPTS']='-Xmx6g';started=time.time()
            print(f'START REFINE target={target} capacity={capacity:.0f}',flush=True)
            with (out/f'{name}.driver.log').open('w') as log:
                result=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
            root=args.run_output_root.resolve()/name
            logs='\n'.join(f.read_text(errors='replace') for f in (root/'logs').glob('*.log'))
            if result.returncode or '[ERROR]' in logs or 'Unresolved compilation' in logs:
                raise RuntimeError(f'Refinement generation failed: {name}')
            summary=ca.summarize_scenario(root);origins=oc.pflow_origin_counts(root,units)['facility']
            comp=oc.compare({'pt':initial['pt_raw_counts'],'facility':origins},'pt')['series']['facility']
            shares=oc.normalize(origins,exclude=[oc.OUTSIDE_AICHI]);classes=Counter()
            for unit,share in shares.items():classes[oc.unit_class(unit)]+=share
            n=summary['target_facility']
            rec={'name':name,'target':int(target),'mesh_addition':first['mesh_addition'],
                 'capacity':capacity,'arrivals_sample':n,'arrivals_full_equivalent':n,
                 'count_relative_error':abs(n-int(target))/int(target),
                 'tvd_aichi':comp['tvd_vs_reference_aichi_only'],'tvd_all':comp['tvd_vs_reference'],
                 'class_shares_aichi':dict(classes),'class_tvd_aichi':oc.total_variation_distance(dict(classes),initial['pt_aichi_class_shares']),
                 'summary':summary,'origin_counts':origins,'scenario':str(sp),'output_dir':str(root),
                 'command':cmd,'elapsed_seconds':time.time()-started}
            atomic_json(out/f'{name}.metrics.json',rec)
            print(f"DONE REFINE target={target} actual={n} TVD={rec['tvd_aichi']:.3f}",flush=True)
            return rec
        try:
            if args.pilot_report:
                first=evaluate(first['capacity'],'initial')
            history=refine_target(first,evaluate,args.count_tolerance,args.max_rounds)
        except BaseException as e:
            report.update(status='failed',error=str(e));atomic_json(out/'report.json',report);raise
        report['trials'].extend(history)
        best=dict(choose_best(history,int(target),args.count_tolerance))
        best['count_met']=best['count_relative_error']<=args.count_tolerance
        best['distribution_met']=best['tvd_aichi']<=report['distribution_tolerance']
        best['joint_met']=best['count_met'] and best['distribution_met']
        report['best'][target]=best;atomic_json(out/'report.json',report)
    report['status']='finished';atomic_json(out/'report.json',report)
    print(json.dumps({'status':report['status'],'best':{t:{k:r[k] for k in ('arrivals_sample','count_relative_error','tvd_aichi','joint_met')} for t,r in report['best'].items()}},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
