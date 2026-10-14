#!/usr/bin/env python3
"""Audit retail attributes without inventing a large-store classification."""
import argparse,csv,hashlib,json
from collections import Counter,defaultdict
from pathlib import Path

def audit(facilities,output):
 if output.exists():raise ValueError('Fresh output required')
 with (facilities/'city_boundary.csv').open() as f:
  valid={r['n03_007'] for r in csv.DictReader(f) if len(r['n03_007'])==5 and r['n03_007'].startswith('23')}
 codes=Counter();points=defaultdict(set);examples=defaultdict(list);rows=0
 with (facilities/'city_retail.csv').open() as f:
  reader=csv.DictReader(f);columns=reader.fieldnames
  for r in reader:
   if r['ADDCD'] not in valid:continue
   try:float(r['LON']);float(r['LAT'])
   except ValueError:continue
   key=r['BUSC'];codes[key]+=1;rows+=1;points[key].add((r['ADDCD'],r['LON'],r['LAT']))
   if len(examples[key])<6:examples[key].append(r['DN'])
 output.mkdir(parents=True)
 with (output/'business_code_inventory.csv').open('w') as f:
  w=csv.writer(f);w.writerow(['business_code','retail_rows','distinct_city_coordinates','example_names','classification_status'])
  for k,n in sorted(codes.items()):w.writerow([k,n,len(points[k]),' / '.join(examples[k]),'unverified_not_a_large_store_label'])
 result={'source':str((facilities/'city_retail.csv').resolve()),'sha256':hashlib.sha256((facilities/'city_retail.csv').read_bytes()).hexdigest(),
         'columns':columns,'valid_aichi_city_count':len(valid),'valid_aichi_retail_rows':rows,'distinct_business_codes':len(codes),
         'classification':'not_available_in_current_loader','loader_existing_capacity':10000,
         'warning':'Business code meanings, physical-site deduplication, floor area and dated classification remain unverified'}
 (output/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--facilities',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();audit(a.facilities,a.output)
