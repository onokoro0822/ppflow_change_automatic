"""Read-only paired A comparisons; unmatched activities reported separately."""
from collections import Counter,defaultdict
from pathlib import Path
from itertools import groupby
from pflow_capacity_analysis import third_mesh_code

def people(path,target):
    for pid,rows in groupby(target.rows(path),key=lambda x:x[0]['person_id']):yield pid,list(rows)

def compare(before,after,target):
    counts=Counter();changes=Counter();dest=Counter();mesh=Counter()
    keys=['person_id','age','gender','labor','start','duration','purpose']
    for p in sorted((Path(before)/'23').glob('*.csv')):
        a=dict(people(p,target));b=dict(people(Path(after)/'23'/p.name,target))
        for pid in a.keys()|b.keys():
            old=a.get(pid,[]);new=b.get(pid,[])
            visit=any(r['purpose']==100 and target.matches(r,t) for r,t in new)
            if [[r[k] for k in keys] for r,_ in old]!=[[r[k] for k in keys] for r,_ in new]:
                counts['unaligned_persons']+=1
                if not visit:counts['unaligned_nonvisitor_persons']+=1
                continue
            counts['aligned_persons']+=1;changed=False
            for (x,_),(y,tr) in zip(old,new):
                if x['purpose']!=100:continue
                if (x['lon'],x['lat'],x['gcode'])==(y['lon'],y['lat'],y['gcode']):continue
                changed=True;counts['changed_shopping_events']+=1
                if not visit:counts['nonvisitor_changed_shopping_events']+=1
                for sign,r in [(-1,x),(1,y)]:
                    dest[r['gcode']]+=sign;mesh[third_mesh_code(r['lon'],r['lat'])]+=sign
                changes[(x['gcode'],y['gcode'])]+=1
            if changed:
                counts['changed_shopping_persons']+=1
                if not visit:counts['nonvisitor_changed_shopping_persons']+=1
    return dict(counts=counts,aligned_city_net=dest,aligned_mesh_net=mesh,aligned_city_transfers=[dict(origin_city=a,destination_city=b,count=n) for (a,b),n in changes.most_common()],notes=['Unaligned activity sequences are reported separately, not paired by row.','Nonvisitor refers to no new-facility ID in final output.','Same-coordinate choices of a different retail ID are not spatial destination changes.'])
