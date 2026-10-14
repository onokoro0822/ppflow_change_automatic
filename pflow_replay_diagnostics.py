#!/usr/bin/env python3
"""Read-only paired diagnostics for the previous commercial-attraction runs.
Temporal pre/post labels describe changes, not causal identification.
"""
import argparse,csv,json
from pathlib import Path
from collections import Counter
from itertools import groupby,zip_longest
from pflow_capacity_analysis import third_mesh_code,TARGET_MESH,TARGET_GCODE,TARGET_LON,TARGET_LAT
from pflow_store_inflow_audit import load_store_codes,point_key

def people(path):
 with path.open() as f:
  for pid,rows in groupby((s.rstrip().split(',') for s in f),key=lambda r:int(r[0])):yield pid,list(rows)

def facility(r):return r[6]=='100' and abs(float(r[7])-TARGET_LON)<1e-6 and abs(float(r[8])-TARGET_LAT)<1e-6

def category(r,locations):
 if facility(r):return 'new_facility'
 if third_mesh_code(float(r[7]),float(r[8]))==TARGET_MESH:return 'target_mesh_existing'
 codes=locations.get(point_key(r[7],r[8]),set())
 if len(codes)!=1:return 'unknown_store_city'
 return 'other_mesh_target_city' if next(iter(codes))==TARGET_GCODE else 'outside_target_city'

def audit(before,after,locations):
 totals={k:Counter() for k in ['before','after','net']};stages=Counter();mesh=Counter();unaligned=0
 for p in sorted((before/'23').glob('*.csv')):
  # File-sized dictionaries tolerate missing/failed people in the historical regeneration.
  old=dict(people(p));new=dict(people(after/'23'/p.name))
  for label,data in [('before',old),('after',new)]:
   for rows in data.values():
    for r in rows:
     if r[6]=='100':totals[label][category(r,locations)]+=1
  for pid in old.keys()|new.keys():
   a,b=old.get(pid,[]),new.get(pid,[])
   if [r[:7] for r in a]!=[r[:7] for r in b]:unaligned+=1;continue
   visits=[i for i,r in enumerate(b) if facility(r)];first=min(visits) if visits else None
   for i,(x,y) in enumerate(zip(a,b)):
    if x[6]!='100' or x[7:]==y[7:]:continue
    phase='no_facility_visit' if first is None else ('pre_visit' if i<first else ('facility_arrival' if facility(y) else 'post_visit'))
    stages[phase]+=1
    for sign,r in [(-1,x),(1,y)]:mesh[(phase,third_mesh_code(float(r[7]),float(r[8])))]+=sign
 for k in totals['before'].keys()|totals['after'].keys():totals['net'][k]=totals['after'][k]-totals['before'][k]
 return {'totals':totals,'changed_shopping_events_by_temporal_stage':stages,'unaligned_persons':unaligned},mesh

def main():
 p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.exists():raise ValueError('Fresh output required')
 a.output.mkdir(parents=True);report=json.loads(a.report.read_text());results={}
 baseline=Path(report['baseline'])
 for t,b in report['best'].items():
  print('AUDIT OLD',t,flush=True)
  config=json.loads(Path(b['scenario']).read_text());locations=load_store_codes(Path('/Volumes/PFLOW_SSD/PFLOW/data/facilities/city_retail.csv'),config)
  results[t],mesh=audit(baseline,Path(b['output_dir']),locations)
  with (a.output/f'mesh_{t}.csv').open('w') as f:
   w=csv.writer(f);w.writerow(['phase','mesh','net_shopping_arrivals']);w.writerows((x,y,n) for (x,y),n in sorted(mesh.items()) if n)
  (a.output/'report.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
  print('DONE OLD',t,json.dumps(results[t]),flush=True)
if __name__=='__main__':main()
