#!/usr/bin/env python3
"""Bounded simulation calibration of facility arrivals and supplied PT origins.

No destination reselection is performed. Java coefficients stay fixed. Search
mesh employee increments and facility weights, rerunning the actual generator.
PT is a calibration assumption, not independent validation. Failure to meet the
specified tolerances is returned explicitly, with the best observed candidate.
"""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import chukyo_pt_origin_distribution as pt
import pflow_capacity_analysis as ca
import pflow_origin_comparison as oc

PROJECT = Path(__file__).resolve().parent
ARGS = ('--shopping-attraction-beta 0.2421875 '
        '--shopping-mnl-multipliers 0.953125,0.03125,-2.84375,-0.8046875,0 '
        '--shopping-local-mesh-capacity raw --facility-city-match on '
        '--large-store-facility-only on --shopping-large-store-accessibility 2,0 '
        '--shopping-large-store 0.22389070848047118,0.3984375,0.45,-0.15625,-12.3515625,-0.03125,0')


def atomic_json(path, payload):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def next_capacity(history, target, competing_capacity):
    """Bracketed log interpolation, otherwise a damped capacity-share inverse."""
    lower = [r for r in history if r['arrivals_sample'] < target]
    upper = [r for r in history if r['arrivals_sample'] > target]
    if lower and upper:
        pairs = [(a, b) for a in lower for b in upper if a['capacity'] < b['capacity']]
        if pairs:
            a, b = min(pairs, key=lambda p: math.log(p[1]['capacity']/p[0]['capacity']))
            frac = (target-a['arrivals_sample'])/(b['arrivals_sample']-a['arrivals_sample'])
            value = math.exp(math.log(a['capacity']) + frac*math.log(b['capacity']/a['capacity']))
            if min(abs(value-a['capacity']), abs(value-b['capacity'])) < 1000:
                value = math.sqrt(a['capacity']*b['capacity'])
            return max(1000, round(value/1000)*1000)
    last = history[-1]
    n, f = last['arrivals_sample'], last['capacity']
    if n <= 0:
        return min(1e9, f*4)
    ceiling = n*(competing_capacity+f)/f
    if target >= ceiling:
        value = f*4
    else:
        value = competing_capacity*target/(ceiling-target)
        value = max(f/4, min(f*4, value))
    return max(1000, min(1e9, round(value/1000)*1000))


def choose_best(rows, target, count_tolerance):
    eligible = [r for r in rows if abs(r['arrivals_full_equivalent']-target)/target <= count_tolerance]
    if eligible:
        return min(eligible, key=lambda r: (r['tvd_aichi'], abs(r['arrivals_full_equivalent']-target)))
    return min(rows, key=lambda r: (abs(r['arrivals_full_equivalent']-target)/target)**2 + r['tvd_aichi']**2)


def audit_transfers(directory, baseline):
    counts = Counter()
    for f in sorted((directory/'23').glob('*.csv')):
        b = baseline/'23'/f.name
        if not b.exists():
            counts['missing_baseline_file'] += 1
            continue
        original = b.read_text(encoding='utf-8').splitlines()
        rows = f.read_text(encoding='utf-8').splitlines()
        if len(original) != len(rows):
            counts['files_with_row_count_change'] += 1
        for i, line in enumerate(rows):
            row = line.split(',')
            if len(row) != 10 or int(row[6]) != ca.SHOPPING_PURPOSE:
                continue
            if abs(float(row[7])-ca.TARGET_LON)>ca.FACILITY_TOLERANCE or abs(float(row[8])-ca.TARGET_LAT)>ca.FACILITY_TOLERANCE:
                continue
            if i >= len(original):
                counts['unaligned_facility_arrival'] += 1
                continue
            old = original[i].split(',')
            if row[:7] != old[:7]:
                counts['unaligned_facility_arrival'] += 1
                continue
            if ca.third_mesh_code(float(old[7]),float(old[8])) == ca.TARGET_MESH:
                counts['same_mesh'] += 1
            elif old[9] == ca.TARGET_GCODE:
                counts['other_mesh_same_selected_city'] += 1
            else:
                counts['other_selected_city'] += 1
    return dict(counts)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--targets', type=int, nargs='+', default=[3000,6000])
    p.add_argument('--pt-csv', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--pflow-home', type=Path, default=PROJECT/'.local/pflow')
    p.add_argument('--baseline', type=Path, default=PROJECT/'.local/pflow/output/large_store_20261014/p2d045/seed42/baseline')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--sample-factor', type=int, default=50)
    p.add_argument('--mesh-additions', type=float, nargs='+', default=[745.942954248366,6000])
    p.add_argument('--competing-capacity', type=float, default=3220000)
    p.add_argument('--reference-ceiling', type=float, default=360, help='Arrivals in the saved 2% reference run')
    p.add_argument('--reference-sample-factor', type=int, default=50)
    p.add_argument('--rounds', type=int, default=3)
    p.add_argument('--workers', type=int, default=1)
    p.add_argument('--count-tolerance', type=float, default=.10)
    p.add_argument('--tvd-tolerance', type=float, default=.15)
    p.add_argument('--verify-full', action='store_true', help='Run full-population count verification and automatic refinement after pilot')
    p.add_argument('--full-pflow-home', type=Path, default=Path('/Volumes/PFLOW_SSD/PFLOW'))
    p.add_argument('--full-count-tolerance', type=float, default=.02)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError('Use a new output directory; existing runs are never overwritten')
    if any(n<=0 for n in args.targets) or args.sample_factor<=0 or args.rounds<1:
        raise ValueError('Targets, sample factor and rounds must be positive')
    out = args.output.resolve(); out.mkdir(parents=True)
    (out/'scenarios').mkdir(); (out/'runs').mkdir()
    labels = pt.load_middle_zone_labels(pt.DEFAULT_ZONE_CODE_XLSX)
    units = oc.ComparisonUnits(labels)
    reference = oc.pt_origin_counts(args.pt_csv,units,labels)['pt_shopping']
    ref_aichi = oc.normalize(reference,exclude=[oc.OUTSIDE_AICHI])
    ref_classes = Counter()
    for unit, share in ref_aichi.items(): ref_classes[oc.unit_class(unit)]+=share
    template=json.loads((PROJECT/'config/pflow_capacity/plan_dept2014_f1000000000.json').read_text())
    report = {'status':'running','method':'bounded_simulation_search',
              'targets':args.targets,'sample_factor':args.sample_factor,'seed':args.seed,
              'pflow_args':ARGS,'pt_csv':str(args.pt_csv.resolve()),
              'pt_sha256':hashlib.sha256(args.pt_csv.read_bytes()).hexdigest(),
              'pt_raw_counts':reference,'pt_aichi_normalized':ref_aichi,'pt_aichi_class_shares':dict(ref_classes),
              'mesh_additions':args.mesh_additions,'count_tolerance':args.count_tolerance,
              'tvd_tolerance':args.tvd_tolerance,'round_budget':args.rounds,
              'notes':['PT origin is preceding activity, not residence.',
                       'The reference is ward-wide large-store shopping, not observations of the hypothetical facility.',
                       'Fit PT origins conditional on Aichi; preserve full-reference TVD as a diagnostic.',
                       'The 6000 employee addition is a sensitivity scenario, not an employment forecast.',
                       'MNL coefficients and distance multiplier 0.45 are fixed.',
                       '2% full equivalents are estimates, not full-population executions.',
                       'Transfer classification uses baseline selected-city codes; physical-city inflow needs separate audit.',
                       'Existing facility-only filter can be overwritten by city matching in Java; existing behavior is retained.'],
              'trials':[],'best':{}}
    atomic_json(out/'report.json',report)
    def trial(target,addition,capacity,index):
        name=f't{target}_add{addition:.0f}_r{index}_f{capacity:.0f}'
        scenario=dict(template); scenario['facility']=dict(template['facility'])
        scenario.update(scenarioId=name,meshCapacityAdd=addition)
        scenario['facility']['capacity']=capacity
        sp=out/'scenarios'/f'{name}.json'; atomic_json(sp,scenario)
        cmd=['bash',str(PROJECT/'scripts/run_pflow_capacity_sweep.sh'),'--scenario-file',str(sp),
             '--pflow-home',str(args.pflow_home.resolve()),'--output-root',str(out/'runs'),
             '--seed',str(args.seed),'--sample-factor',str(args.sample_factor),
             '--skip-compile','--pflow-args',ARGS]
        started=time.time(); print(f'START {name}',flush=True)
        env=dict(os.environ); env['MAVEN_OPTS']='-Xmx6g'
        with (out/f'{name}.driver.log').open('w') as log:
            result=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
        root=out/'runs'/name
        logs='\n'.join(f.read_text(errors='replace') for f in (root/'logs').glob('*.log'))
        if result.returncode or '[ERROR]' in logs or 'Unresolved compilation' in logs:
            raise RuntimeError(f'Generation failed; inspect {root}/logs and {name}.driver.log')
        summary=ca.summarize_scenario(root)
        origins=oc.pflow_origin_counts(root,units)['facility']
        comp=oc.compare({'pt':reference,'facility':origins},'pt')['series']['facility']
        shares=oc.normalize(origins,exclude=[oc.OUTSIDE_AICHI])
        classes=Counter()
        for unit,share in shares.items():classes[oc.unit_class(unit)]+=share
        rec={'name':name,'target':target,'mesh_addition':addition,'capacity':capacity,
             'arrivals_sample':summary['target_facility'],
             'arrivals_full_equivalent':summary['target_facility']*args.sample_factor,
             'count_relative_error':abs(summary['target_facility']*args.sample_factor-target)/target,
             'tvd_aichi':comp['tvd_vs_reference_aichi_only'],'tvd_all':comp['tvd_vs_reference'],
             'class_shares_aichi':dict(classes),'origin_counts':origins,
             'origin_outside_or_unknown':origins.get(oc.OUTSIDE_AICHI,0),
             'class_tvd_aichi':oc.total_variation_distance(dict(classes),dict(ref_classes)),
             'summary':summary,'transfer_audit':audit_transfers(root,args.baseline),
             'command':cmd,'output_dir':str(root),'scenario':str(sp),
             'elapsed_seconds':round(time.time()-started,1),
             'generation_failed_person_log_lines':sum('Failed' in line or 'failed' in line for line in logs.splitlines())}
        atomic_json(out/f'{name}.metrics.json',rec)
        print(f"DONE {name}: n={rec['arrivals_full_equivalent']} TVD={rec['tvd_aichi']:.3f}",flush=True)
        return rec
    histories={(target,add):[] for target in args.targets for add in args.mesh_additions}
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for iteration in range(args.rounds):
                jobs=[]
                for (target,addition),history in histories.items():
                    if history and history[-1]['count_relative_error']<=args.count_tolerance: continue
                    if history:
                        cap=next_capacity(history,target/args.sample_factor,args.competing_capacity)
                    else:
                        ceiling=args.reference_ceiling*args.reference_sample_factor/args.sample_factor*(22230+addition)/(22230+args.mesh_additions[0])
                        n=target/args.sample_factor
                        if n>=ceiling: raise ValueError('Initial target exceeds reference ceiling')
                        cap=max(1000,round(args.competing_capacity*n/(ceiling-n)/1000)*1000)
                    jobs.append(((target,addition),pool.submit(trial,target,addition,cap,iteration)))
                for key,future in jobs:
                    rec=future.result(); histories[key].append(rec); report['trials'].append(rec)
                    for target in args.targets:
                        available=[r for r in report['trials'] if r['target']==target]
                        if available:
                            best=dict(choose_best(available,target,args.count_tolerance))
                            best['count_met']=best['count_relative_error']<=args.count_tolerance
                            best['distribution_met']=best['tvd_aichi']<=args.tvd_tolerance
                            best['joint_met']=best['count_met'] and best['distribution_met']
                            report['best'][str(target)]=best
                    atomic_json(out/'report.json',report)
        report['status']='finished'
    except BaseException as e:
        report['status']='failed';report['error']=str(e);atomic_json(out/'report.json',report);raise
    atomic_json(out/'report.json',report)
    with (out/'summary.csv').open('w',newline='',encoding='utf-8-sig') as f:
        fields=['name','target','mesh_addition','capacity','arrivals_sample','arrivals_full_equivalent',
                'count_relative_error','tvd_aichi','class_tvd_aichi','tvd_all','elapsed_seconds']
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(report['trials'])
    print(json.dumps({'status':report['status'],'best':{t:{k:r[k] for k in ('name','arrivals_full_equivalent','tvd_aichi','joint_met')} for t,r in report['best'].items()}},ensure_ascii=False),flush=True)

    if args.verify_full:
        command=[sys.executable,str(PROJECT/'pflow_joint_target_verify.py'),
                 '--pilot-report',str(out/'report.json'),'--output',str(out/'full_refinement'),
                 '--run-output-root',str(args.full_pflow_home.resolve()/'output'/out.name),
                 '--pflow-home',str(args.full_pflow_home.resolve()),
                 '--count-tolerance',str(args.full_count_tolerance)]
        subprocess.run(command,check=True)

if __name__=='__main__':main()
