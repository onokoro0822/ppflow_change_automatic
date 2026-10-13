"""Audit retail-address destinations and unambiguous home-origin inflows.

Shopping destinations use TelePoint ADDCD, not the city selected by the MNL.
Home activities use EPurpose.HOME=1 and the household's city code. Other origin
activities are excluded from home-origin conclusions. Unmapped/collocated
locations retain conservative bounds instead of being imputed.
"""
import argparse
import csv
import json
import math
from collections import Counter,defaultdict
from pathlib import Path
from pflow_capacity_analysis import SHOPPING_PURPOSE,_iter_rows

HOME_PURPOSE=1


def point_key(lon,lat):
    return round(float(lon),6),round(float(lat),6)


def load_store_codes(retail_csv,scenario_config):
    codes=defaultdict(set)
    with retail_csv.open(encoding='utf-8',errors='replace',newline='') as f:
        for row in csv.DictReader(f):
            try:lon,lat=float(row['LON'])-.000293,float(row['LAT'])+.000106950
            except (ValueError,TypeError):continue
            if math.isfinite(lon) and math.isfinite(lat):codes[point_key(lon,lat)].add(row['ADDCD'])
    key=point_key(scenario_config['facility']['longitude'],scenario_config['facility']['latitude'])
    target=scenario_config['target']['gcode']
    if key in codes and codes[key]!={target}:raise ValueError('Injected facility conflicts with store code mapping')
    codes[key]={target}
    return codes


def events(rows,locations,target):
    previous={};result={};counts=Counter()
    for row in rows:
        pid=int(row['person_id']);before=previous.get(pid);previous[pid]=row
        if int(row['purpose'])!=SHOPPING_PURPOSE:continue
        key=(pid,int(row['start']),int(row['purpose']))
        if key in result:raise ValueError('Duplicate shopping key')
        point=point_key(row['lon'],row['lat']);codes=locations.get(point,set())
        store=next(iter(codes)) if len(codes)==1 else None
        home=bool(before and int(before['purpose'])==HOME_PURPOSE)
        origin=str(before['gcode']) if home else None
        eligible=home and origin!=target
        result[key]={'point':point,'store':store,'eligible':eligible,'home_origin':origin,'ambiguous':len(codes)>1}
        counts['shopping_total']+=1
        if str(row['gcode'])==target:
            counts['selected_city_target_arrivals']+=1
            if eligible:counts['selected_city_home_outside_arrivals']+=1
        if store is None:
            counts['unmapped_or_ambiguous']+=1
            counts['ambiguous_store_rows' if len(codes)>1 else 'unmapped_store_rows']+=1
        elif store!=str(row['gcode']):counts['selected_city_differs_from_store']+=1
        if store==target:
            counts['retail_address_target_arrivals']+=1
            if home:counts['direct_home_target_arrivals']+=1
            if eligible:counts['known_home_outside_target_arrivals']+=1
        elif store is None and eligible:counts['unknown_store_home_outside']+=1
    return result,counts


def audit_pair(before_rows,after_rows,locations,target='23105'):
    before,bc=events(before_rows,locations,target);after,ac=events(after_rows,locations,target)
    lower=upper=0;entries=Counter();exits=Counter();origins=Counter();other=Counter()
    def possible(event):
        if event is None or not event['eligible']:return (0,0)
        if event['store'] is None:return (0,1)
        indicator=int(event['store']==target);return indicator,indicator
    for key in before.keys()|after.keys():
        b,a=before.get(key),after.get(key)
        # Identical unknown destinations with identical eligibility cancel exactly.
        if b and a and b['point']==a['point'] and b['eligible']==a['eligible'] and not b['ambiguous'] and not a['ambiguous']:continue
        bmin,bmax=possible(b);amin,amax=possible(a)
        lo,hi=amin-bmax,amax-bmin;lower+=lo;upper+=hi
        if lo!=hi:other['uncertain_changed_events']+=1;continue
        if lo==1:
            if b and b['eligible'] and b['store'] is not None and b['store']!=target:
                entries[b['store']]+=1;origins[a['home_origin']]+=1
            else:other['other_confirmed_entries']+=1
        elif lo==-1:
            if a and a['eligible'] and a['store'] is not None and a['store']!=target:exits[a['store']]+=1
            else:other['other_confirmed_exits']+=1
    return {'before_counts':dict(bc),'after_counts':dict(ac),
            'home_outside_net_lower':lower,'home_outside_net_upper':upper,
            'previous_store_cities_of_confirmed_entries':dict(entries),
            'new_store_cities_of_confirmed_exits':dict(exits),
            'home_cities_of_confirmed_entries':dict(origins),'other_changes':dict(other)}


def audit(baseline,scenario,locations,target='23105'):
    for root in (baseline,scenario):
        if not (root/'.complete').exists():raise ValueError('Run not complete')
    paths=[sorted((root/'23').glob('*.csv')) for root in (baseline,scenario)]
    if not paths[0] or [p.name for p in paths[0]]!=[p.name for p in paths[1]]:raise ValueError('Files differ')
    totals={k:Counter() for k in ('before_counts','after_counts','previous_store_cities_of_confirmed_entries',
                                 'new_store_cities_of_confirmed_exits','home_cities_of_confirmed_entries','other_changes')}
    lower=upper=0
    for b,a in zip(*paths):
        part=audit_pair(_iter_rows(b),_iter_rows(a),locations,target)
        lower+=part['home_outside_net_lower'];upper+=part['home_outside_net_upper']
        for k in totals:totals[k].update(part[k])
    return {'target_store_code':target,'csv_file_count':len(paths[0]),
            'home_outside_net_lower':lower,'home_outside_net_upper':upper,
            **{k:dict(v) for k,v in totals.items()},
            'notes':['Retail address codes are input locations, not observed visitor data.',
                     'Unambiguous outside origins here are HOME activities only.',
                     'Conservative bounds retain unmatched/ambiguous changed locations.',
                     'Identical unmapped locations with unchanged home-origin eligibility cancel; conflicting address codes remain bounded.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--scenario',type=Path,required=True)
    parser.add_argument('--retail-csv',type=Path,default=Path('.local/pflow/data/facilities/city_retail.csv'))
    parser.add_argument('--config',type=Path,default=Path('config/pflow_capacity/plan_dept2014_f8000000.json'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();config=json.loads(args.config.read_text())
    locations=load_store_codes(args.retail_csv,config)
    result=audit(args.baseline,args.scenario,locations,config['target']['gcode'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
