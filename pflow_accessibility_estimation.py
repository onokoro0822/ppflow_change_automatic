"""Compare origin-dependent retail accessibility on held-out PT origin units.

A(o,d)=log(1+sum_m employees_m**power / max(distance(o,m),1km)**decay).
This is an accessibility hypothesis, not a fitted facility visitor target or
an established nested-logit model. Shared mesh values/membership match Java.
"""
import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np

import pflow_mnl_estimation as estimation
from pflow_origin_comparison import ComparisonUnits, pt

CURRENT = np.array([0.40625, 0.90625, -0.0625, -4.65625, -0.453125])


def mesh_center(code):
    return (100+int(code[2:4])+int(code[5])/8+int(code[7])/80+1/160,
            int(code[:2])*2/3+int(code[4])/12+int(code[6])/120+1/240)


def load_meshes(facilities, codes):
    with (facilities/'city_boundary.csv').open() as f:
        valid={r['n03_007'] for r in csv.DictReader(f)}
    capacities={}; memberships={c:set() for c in codes}
    with (facilities/'mesh_ecensus.csv').open() as f:
        for r in csv.DictReader(f):
            if r['n03_007'] not in valid:
                continue
            capacities.setdefault(r['key_code'],float(r['t000917029']))
            if r['n03_007'] in memberships:
                memberships[r['n03_007']].add(r['key_code'])
    meshes=sorted(set().union(*memberships.values()))
    index={m:i for i,m in enumerate(meshes)}
    membership=np.zeros((len(meshes),len(codes)))
    for j,c in enumerate(codes):
        for m in memberships[c]:membership[index[m],j]=1
    return meshes,np.array([capacities[m] for m in meshes]),membership


def accessibility(capacity, distances_km, membership, power, decay):
    if power<=0 or decay<0 or np.any(capacity<0) or not np.isfinite(capacity).all():
        raise ValueError('Invalid accessibility inputs')
    weights=capacity[None,:]**power / np.maximum(distances_km,1.0)**decay
    return np.log1p(weights@membership)


class AccessibilityModel:
    def __init__(self, original, cities, params):
        self.original=original
        self.codes=original.codes
        self.unit_names=original.unit_names
        self.unit_index=original.unit_index
        self.membership=original.membership
        self.origin_weights=original.weights.sum(axis=1)
        totals=np.maximum(self.origin_weights,1)
        self.mixture=original.weights/totals[:,None]
        n=len(self.codes);k=len(original.kinds)
        self.fixed=np.zeros((k,n,n));self.basis=np.zeros((4,k,n,n))
        area=np.array([cities[c]['area'] for c in self.codes])
        pop=np.array([cities[c]['pop_ratio'] for c in self.codes])/1000
        office=np.array([cities[c]['office_ratio'] for c in self.codes])/1000
        for i,kind in enumerate(original.kinds):
            p=np.array([params[kind][int(t)] for t in original.origin_type])
            self.fixed[i]=p[:,2,None]*original.same
            self.basis[0,i]=p[:,0,None]*original.deg
            self.basis[1,i]=p[:,4,None]*area[None,:]
            self.basis[2,i]=p[:,5,None]*pop[None,:]
            self.basis[3,i]=p[:,6,None]*office[None,:]

    def city_probabilities(self, x, feature):
        v=self.fixed+np.einsum('i,ikod->kod',x[1:],self.basis)+x[0]*feature[None,:,:]
        v=np.where(self.original.available[None,:,:],v,-np.inf)
        v-=np.max(v,axis=2,keepdims=True)
        e=np.exp(v);p=e/e.sum(axis=2,keepdims=True)
        return np.sum(p*self.mixture.T[:,:,None],axis=0)

    def probabilities(self,x,feature):
        p=self.city_probabilities(x,feature)@self.membership
        num=self.membership.T@(p*self.origin_weights[:,None])
        den=self.membership.T@self.origin_weights
        return np.divide(num,den[:,None],out=np.zeros_like(num),where=den[:,None]>0)


def row_split(n,seed=13):
    if n<10:raise ValueError('At least ten origin units required')
    idx=np.random.default_rng(seed).permutation(n)
    train=idx[:int(n*.6)];valid=idx[int(n*.6):int(n*.8)];test=idx[int(n*.8):]
    return train,valid,test


def score(prob,od,origins,reachable):
    mask=np.zeros_like(od,dtype=bool);mask[origins]=reachable[origins]
    total=float(od[mask].sum())
    if total<=0:raise ValueError('No reachable observations')
    ll=float((od[mask]*np.log(np.maximum(prob[mask],1e-300))).sum())
    return {'log_likelihood':ll,'mean_log_probability':ll/total,'trips':total}


def fit(model,feature,od,origins,reachable,start=(0.0,1.0,1.0,1.0,1.0)):
    x=np.array(start,dtype=float).copy();step=np.array([.5,.25,.5,.5,.5])
    def value(z):return score(model.probabilities(z,feature),od,origins,reachable)['mean_log_probability']
    best=value(x);evaluations=1
    for _ in range(160):
        improved=False
        for j in range(5):
            for sign in (1,-1):
                z=x.copy();z[j]+=sign*step[j]
                if j==1 and z[j]<=.05:continue
                candidate=value(z);evaluations+=1
                if candidate>best+1e-10:best=candidate;x=z;improved=True
        if not improved:step/=2
        if max(step)<.01:break
    return x,{'evaluations':evaluations,'final_step_max':float(max(step)),
               'converged_step':bool(max(step)<.01)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('output/accessibility_estimation/comparison.json'))
    args=parser.parse_args()
    facilities=Path('.local/pflow/data/facilities')
    cities=estimation.load_cities(facilities);params=estimation.load_params(facilities/'mnl')
    units=ComparisonUnits(pt.load_middle_zone_labels(pt.DEFAULT_ZONE_CODE_XLSX))
    weights=estimation.chooser_weights(Path('.local/pflow/output/capacity_experiment/baseline/23'))
    original=estimation.DestinationModel(cities,params,weights,units)
    model=AccessibilityModel(original,cities,params)
    # Verify the vectorised implementation against the existing MNL, before fitting.
    reference_model=estimation.DestinationModel(
        cities,estimation.scaled_params(params,*CURRENT[2:]),weights,units)
    reference_probability=reference_model.unit_probabilities(CURRENT[0],CURRENT[1])
    base_feature=np.broadcast_to(original.ln_retail,(len(model.codes),len(model.codes)))
    np.testing.assert_allclose(model.probabilities(CURRENT,base_feature),
                               np.nan_to_num(reference_probability),rtol=1e-12,atol=1e-14)
    od=estimation.pt_unit_od(pt.DEFAULT_INPUT,units,model.unit_names)
    train,valid,test=row_split(len(model.unit_names))
    reachable=model.probabilities(CURRENT,np.broadcast_to(original.ln_retail,(len(model.codes),len(model.codes))))>0
    meshes,cap,membership=load_meshes(facilities,model.codes)
    centers=[mesh_center(m) for m in meshes]
    distances=np.array([[estimation.haversine_m(cities[c]['lon'],cities[c]['lat'],lon,lat)/1000
                         for lon,lat in centers] for c in model.codes])
    candidates=[]
    features={}
    for power in (.5,1.,1.5,2.):
        for decay in (0.,1.,2.):
            feature=accessibility(cap,distances,membership,power,decay)
            x,diagnostic=fit(model,feature,od,train,reachable)
            probability=model.probabilities(x,feature)
            candidates.append({'power':power,'decay':decay,'coefficients':x.tolist(),
                               'train':score(probability,od,train,reachable),
                               'validation':score(probability,od,valid,reachable),'fit':diagnostic})
            features[(power,decay)]=feature
            print('candidate',power,decay,'validation',candidates[-1]['validation']['mean_log_probability'],flush=True)
    best=max(candidates,key=lambda c:c['validation']['mean_log_probability'])
    baseline=next(c for c in candidates if c['power']==1 and c['decay']==0)
    for item in (best,baseline):
        item['test']=score(model.probabilities(np.array(item['coefficients']),features[(item['power'],item['decay'])]),od,test,reachable)
    feature=features[(best['power'],best['decay'])]
    final,diagnostic=fit(model,feature,od,np.arange(len(model.unit_names)),reachable,start=best['coefficients'])
    changed=cap.copy();changed[meshes.index('52366700')]+=745.942954248366
    intervention=accessibility(changed,distances,membership,best['power'],best['decay'])
    before=model.city_probabilities(final,feature);after=model.city_probabilities(final,intervention)
    target=model.codes.index('23105');flows=[]
    for i,code in enumerate(model.codes):
        if code=='23105':continue
        flows.append({'origin':code,'before':float(before[i,target]*model.origin_weights[i]),
                      'after':float(after[i,target]*model.origin_weights[i])})
    outside_weights=model.origin_weights.copy();outside_weights[target]=0
    destination_delta=outside_weights@(after-before)
    donor_changes=[{'destination':code,'expected_outside_chooser_arrival_delta':float(destination_delta[i])}
                   for i,code in enumerate(model.codes)]
    results={'status':'prototype_not_java_simulation','split_seed':13,
             'fit_initialization':[0,1,1,1,1],
             'split_origin_units':{k:[model.unit_names[i] for i in indices] for k,indices in [('train',train),('validation',valid),('test',test)]},
             'unreachable_pt_share':float(od[~reachable].sum()/od.sum()),'candidates':candidates,
             'selected':best,'baseline_train_fitted':baseline,
             'all_pt_refit':{'coefficients':final.tolist(),'diagnostic':diagnostic},
             'intervention':{'mesh':'52366700','employee_add':745.942954248366,
                             'expected_outside_arrivals_before':sum(x['before'] for x in flows),
                             'expected_outside_arrivals_after':sum(x['after'] for x in flows),'origin_flows':flows,
                             'destination_changes_for_outside_choosers':donor_changes},
             'notes':['Internal held-out aggregated PT origins, not external facility validation.',
                      'All-PT refit is for prospective simulation, not held-out evaluation.',
                      'Expected flow uses fixed original-baseline chooser counts; no new shopping activities.',
                      'PT destinations are physical areas; this surrogate MNL predicts selected cities. Full simulator validation must reconcile retail-address destinations.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print('selected',best['power'],best['decay'],'test',best['test'],'baseline_test',baseline['test'])
    print('refit',final.tolist(),'outside_expected_delta',sum(x['after']-x['before'] for x in flows))


if __name__=='__main__':main()
