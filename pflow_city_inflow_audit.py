"""Attribute changes in ward shopping inflows between paired activity runs.

Pairs are keyed by person, activity start and purpose. Unmatched shopping
activities and changes of preceding location are reported separately.
No observed facility OD is imposed and no individual IDs are exported.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from pflow_capacity_analysis import SHOPPING_PURPOSE, _iter_rows


def shopping_events(rows):
    previous={};events={}
    for row in rows:
        pid=int(row['person_id']);before=previous.get(pid);previous[pid]=row
        if int(row['purpose'])!=SHOPPING_PURPOSE:continue
        key=(pid,int(row['start']),int(row['purpose']))
        if key in events:raise ValueError('Duplicate shopping activity key')
        events[key]={'destination':str(row['gcode']),
                     'origin':str(before['gcode']) if before else None}
    return events


def audit_pair(before_rows,after_rows,target='23105'):
    before=shopping_events(before_rows);after=shopping_events(after_rows)
    def incoming(e):return e['destination']==target and e['origin'] is not None and e['origin']!=target
    counts=Counter(incoming_before=sum(incoming(e) for e in before.values()),
                   incoming_after=sum(incoming(e) for e in after.values()))
    entries=Counter();exits=Counter();origins=Counter()
    for key in before.keys() & after.keys():
        b,a=before[key],after[key]
        if a['destination']==target and b['destination']!=target and incoming(a):
            counts['destination_entries']+=1;entries[b['destination']]+=1;origins[a['origin']]+=1
        elif b['destination']==target and a['destination']!=target and incoming(b):
            counts['destination_exits']+=1;exits[a['destination']]+=1
        elif a['destination']==b['destination']==target:
            counts['origin_classification_delta']+=int(incoming(a))-int(incoming(b))
    for key in before.keys()-after.keys():
        counts['unmatched_before_shopping']+=1
        counts['unmatched_incoming_before']+=int(incoming(before[key]))
    for key in after.keys()-before.keys():
        counts['unmatched_after_shopping']+=1
        counts['unmatched_incoming_after']+=int(incoming(after[key]))
    delta=counts['incoming_after']-counts['incoming_before']
    explained=counts['destination_entries']-counts['destination_exits']+counts['origin_classification_delta']+counts['unmatched_incoming_after']-counts['unmatched_incoming_before']
    assert delta==explained,(delta,explained)
    return {'counts':dict(counts),'previous_destinations_of_entries':dict(entries),
            'new_destinations_of_exits':dict(exits),'origins_of_entries':dict(origins)}


def audit(baseline,scenario):
    files=[sorted((root/'23').glob('*.csv')) for root in (baseline,scenario)]
    if not all((root/'.complete').exists() for root in (baseline,scenario)):
        raise ValueError('Both runs must be complete')
    if [p.name for p in files[0]]!=[p.name for p in files[1]] or not files[0]:
        raise ValueError('Paired CSV files differ or are missing')
    totals={key:Counter() for key in ('counts','previous_destinations_of_entries','new_destinations_of_exits','origins_of_entries')}
    for before,after in zip(*files):
        result=audit_pair(_iter_rows(before),_iter_rows(after))
        for key in totals:totals[key].update(result[key])
    return {'target_gcode':'23105','csv_file_count':len(files[0]),
            **{k:dict(v) for k,v in totals.items()},
            'notes':['Destination and origin are output city codes, not verified building locations.',
                     'Origins are preceding activities, not residences.',
                     'Unmatched activities are retained in the net-flow reconciliation.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--scenario',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=audit(args.baseline,args.scenario)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
