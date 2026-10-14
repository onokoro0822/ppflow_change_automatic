#!/usr/bin/env python3
"""Quota-conditioned facility scenarios with fixed baseline activities.
PT is an assumed origin composition, never independent validation.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,math,os,shlex,subprocess
from collections import Counter
from pathlib import Path
from itertools import zip_longest
import chukyo_pt_origin_distribution as pt
import pflow_origin_comparison as oc
from pflow_joint_target_calibration import ARGS,atomic_json
from pflow_capacity_analysis import TARGET_LON,TARGET_LAT,TARGET_GCODE,TARGET_MESH,third_mesh_code
PROJECT=Path(__file__).resolve().parent


def apportion(total,shares):
    if total<0 or int(total)!=total or not shares or any(not math.isfinite(x) or x<0 for x in shares.values()) or sum(shares.values())<=0:
        raise ValueError('Nonnegative integer target and finite positive share sum required')
    weights={k:total*v/sum(shares.values()) for k,v in shares.items()}
    result={k:math.floor(v) for k,v in weights.items()}
    for k in sorted(weights,key=lambda k:(-(weights[k]-result[k]),k))[:total-sum(result.values())]:result[k]+=1
    return result


def traced_rows(path):
    with path.open() as data, Path(str(path)+'.branches.tsv').open() as trace:
        reader=csv.DictReader(trace,delimiter='\t');previous=None;index=0
        for line,tr in zip_longest(data,reader):
            if line is None or tr is None:raise ValueError(f'Trace length mismatch: {path}')
            r=line.rstrip('\n').split(',')
            if len(r)!=10:raise ValueError('Expected ten activity columns')
            if previous is None or previous[0]!=r[0]:index=0;previous=None
            if [tr['person_id'],tr['activity_index'],tr['start'],tr['purpose']]!=[r[0],str(index),r[4],r[6]]:
                raise ValueError(f'Trace identity mismatch: {path}')
            if r[6]=='100' and tr['large_store'] not in ('0','1'):raise ValueError('Missing shopping branch')
            yield r,previous,tr['large_store']=='1'
            previous=r;index+=1


def inventory(baseline,unit_map):
    available=Counter();files={};activities=0
    paths=sorted((baseline/'23').glob('*.csv'))
    if not paths:raise ValueError('No baseline CSV')
    for p in paths:
        for row,prev,large in traced_rows(p):
            activities+=1
            if large and row[6]=='100' and prev is not None and prev[9] in unit_map:available[unit_map[prev[9]]]+=1
        files[p.name]={'csv_sha256':hashlib.file_digest(p.open('rb'),'sha256').hexdigest(),
                      'trace_sha256':hashlib.file_digest(Path(str(p)+'.branches.tsv').open('rb'),'sha256').hexdigest()}
    return {'available':dict(available),'files':files,'activities':activities}


def validate_output(baseline,out,config):
    """Independent row alignment + event-reason + final-O checks; no inferred branch."""
    reasons={};counts=Counter();origins=Counter();mesh_effect=Counter();city_effect=Counter()
    with (out/'audit.tsv').open() as f:
        for a in csv.DictReader(f,delimiter='\t'):
            reasons[(a['file'],a['person_id'],int(a['activity_index']))]=a
    for p in sorted((baseline/'23').glob('*.csv')):
        prev=None;idx=0;induced=False;pid=None;person_changed=False
        with (out/'23'/p.name).open() as new:
            for triple,line in zip_longest(traced_rows(p),new):
                if triple is None or line is None:raise AssertionError('Activity count changed')
                old,_,large=triple;row=line.rstrip('\n').split(',')
                if old[:7]!=row[:7]:raise AssertionError('Activity attributes/schedule changed')
                if pid!=old[0]:
                    if pid is not None:
                        counts['persons']+=1;counts['induced_persons']+=induced
                        if person_changed and not induced:raise AssertionError('Non-induced person changed')
                    pid=old[0];idx=0;prev=None;induced=False;person_changed=False
                key=(p.name,pid,idx);audit=reasons.get(key,{});reason=audit.get('reason','unchanged')
                changed=old[7:]!=row[7:]
                is_fac=row[6]=='100' and abs(float(row[7])-config['longitude'])<1e-6 and abs(float(row[8])-config['latitude'])<1e-6
                if is_fac:
                    if row[9]!=config['gcode']:raise AssertionError('Facility city mismatch')
                    if third_mesh_code(float(row[7]),float(row[8]))!=config['meshCode']:raise AssertionError('Facility mesh mismatch')
                    if not large or prev is None:raise AssertionError('Ordinary or originless facility arrival')
                    unit=config['units'].get(prev[9]);origins[unit]+=1;counts['facility_arrivals']+=1;induced=True
                    if reason not in ('direct_facility','repeat_facility'):raise AssertionError('Unrecorded arrival')
                if changed:
                    if not induced:raise AssertionError('Pre-induction destination changed')
                    if int(row[6])<100:raise AssertionError('Fixed destination changed')
                    if reason not in ('direct_facility','repeat_facility','downstream'):raise AssertionError('Unexplained change')
                    counts['changed_destinations']+=1;person_changed=True
                    counts['direct_destination_changes' if is_fac else 'downstream_destination_changes']+=1
                    effect='direct' if is_fac else 'downstream'
                    for sign,r in [(-1,old),(1,row)]:
                        mesh_effect[(effect,third_mesh_code(float(r[7]),float(r[8])))]+=sign
                        city_effect[(effect,r[9])]+=sign
                if reason=='selection_failed_retained':counts['selection_failures']+=1
                if prev is not None and audit:
                    if [audit['origin_lon'],audit['origin_lat'],audit['origin_gcode']]!=prev[7:]:raise AssertionError('Audited origin mismatch')
                counts['activities']+=1;prev=row;idx+=1
        if pid is not None:
            counts['persons']+=1;counts['induced_persons']+=induced
            if person_changed and not induced:raise AssertionError('Non-induced person changed')
    if any(v>config['quotas'].get(k,0) for k,v in origins.items()):raise AssertionError('Quota exceeded or unknown origin')
    for name,effects in [('mesh',mesh_effect),('city',city_effect)]:
        with (out/f'{name}_effects.csv').open('w') as f:
            w=csv.writer(f);w.writerow(['effect',name,'net_activity_arrivals'])
            w.writerows((a,b,n) for (a,b),n in sorted(effects.items()) if n)
    if sum(v for (effect,_),v in mesh_effect.items() if effect=='direct')!=0:raise AssertionError('Direct balance mismatch')
    if not config['downstream'] and counts.get('downstream_destination_changes',0):raise AssertionError('Unexpected downstream change')
    counts.update({'schedule_mismatches':0,'fixed_destination_mismatches':0,'noninduced_person_mismatches':0,'ordinary_facility_arrivals':0})
    return {'audit':dict(counts),'actual_origins':dict(origins)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--pt-csv',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--pflow-home',type=Path,required=True)
    p.add_argument('--targets',type=int,nargs='+',default=[3000,6000]);p.add_argument('--sample-factor',type=int,default=1)
    p.add_argument('--seed',type=int,default=42);p.add_argument('--direct-only',action='store_true')
    p.add_argument('--longitude',type=float,default=TARGET_LON);p.add_argument('--latitude',type=float,default=TARGET_LAT)
    p.add_argument('--gcode',default=TARGET_GCODE);p.add_argument('--mesh-code',default=TARGET_MESH)
    a=p.parse_args()
    if a.output.exists():raise ValueError('Use fresh output directory')
    if not math.isfinite(a.longitude) or not math.isfinite(a.latitude) or abs(a.longitude)>180 or abs(a.latitude)>90:raise ValueError('Invalid facility coordinates')
    if third_mesh_code(a.longitude,a.latitude)!=a.mesh_code:raise ValueError('Facility coordinates and mesh disagree')
    if len(set(a.targets))!=len(a.targets):raise ValueError('Duplicate targets')
    if a.sample_factor<1 or any(t<0 for t in a.targets):raise ValueError('Invalid targets/sample factor')
    units=oc.ComparisonUnits(pt.load_middle_zone_labels(pt.DEFAULT_ZONE_CODE_XLSX))
    mapping={g:units.unit_for_gcode(g) for g in oc.AICHI_MUNICIPALITIES}
    reference=oc.pt_origin_counts(a.pt_csv,units,pt.load_middle_zone_labels(pt.DEFAULT_ZONE_CODE_XLSX))['pt_shopping']
    shares=oc.normalize(reference,exclude=[oc.OUTSIDE_AICHI])
    a.output.mkdir(parents=True);print('INVENTORY',flush=True)
    inv=inventory(a.baseline,mapping);atomic_json(a.output/'inventory.json',inv)
    report={'status':'running','seed':a.seed,'sample_factor':a.sample_factor,'baseline':str(a.baseline.resolve()),
            'pt_shares':shares,'pt_sha256':hashlib.file_digest(a.pt_csv.open('rb'),'sha256').hexdigest(),
            'model_args':shlex.split(ARGS),
            'source_sha256':{str(p.relative_to(PROJECT.parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),PROJECT.parent/'Pseudo-PFLOW-v3/src/pseudo/gen/FixedActivityReplay.java',PROJECT.parent/'Pseudo-PFLOW-v3/src/pseudo/gen/ActGenerator.java']},
            'method':'sequential_quota_conditioned_replay','pt_role':'assumed_input_not_validation','runs':[]}
    atomic_json(a.output/'report.json',report)
    for target in a.targets:
        n=round(target/a.sample_factor);quota=apportion(n,shares);run=a.output/f'target{target}'
        config={'baseline':str(a.baseline.resolve()),'output':str(run.resolve()),'pflowHome':str(a.pflow_home.resolve()),
                'seed':a.seed,'longitude':a.longitude,'latitude':a.latitude,'gcode':a.gcode,'meshCode':a.mesh_code,
                'downstream':not a.direct_only,'units':mapping,'quotas':quota,'available':inv['available'],'modelArgs':shlex.split(ARGS)}
        cp=a.output/f'target{target}.json';atomic_json(cp,config)
        cmd=['mvn','-q','-DskipTests','exec:java','-Dexec.mainClass=pseudo.gen.FixedActivityReplay','-Dexec.args='+str(cp.resolve())]
        print('REPLAY',target,flush=True)
        with (a.output/f'target{target}.log').open('w') as log:
            subprocess.run(cmd,cwd=PROJECT.parent/'Pseudo-PFLOW-v3',env=dict(os.environ,MAVEN_OPTS='-Xmx6g'),stdout=log,stderr=subprocess.STDOUT,check=True)
        logs=(a.output/f'target{target}.log').read_text(errors='replace')
        if '[ERROR]' in logs or 'Unresolved compilation' in logs or not (run/'.complete').exists():raise RuntimeError('Replay failed')
        result=json.loads((run/'replay_summary.json').read_text());print('AUDIT',target,flush=True)
        result.update(validate_output(a.baseline,run,config))
        actual=result['audit'].get('facility_arrivals',0);error=abs(actual*a.sample_factor-target)/target if target else float(actual!=0)
        result.update(target=target,actual_sample=actual,actual_full_equivalent=actual*a.sample_factor,count_relative_error=error,
                      count_met=error<=.02,quota_exact=all(result['actual_origins'].get(k,0)==v for k,v in quota.items()),
                      tvd=oc.total_variation_distance(oc.normalize(result['actual_origins']),shares) if actual else None,output=str(run.resolve()),command=cmd)
        result['audit']['status']='passed';atomic_json(run/'verified.json',result)
        report['runs'].append(result);atomic_json(a.output/'report.json',report)
        print('DONE',target,'actual',actual,'TVD',result['tvd'],flush=True)
    report['status']='finished';atomic_json(a.output/'report.json',report)

if __name__=='__main__':main()
