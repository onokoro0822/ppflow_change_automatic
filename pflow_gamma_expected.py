"""Expected city -> mesh -> facility choices over baseline shopping events.

Inputs only; never edits activity CSVs. The approximation freezes previous O
and activity demand, then actual Java regeneration measures chain feedback.
"""
import csv,json,math
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
import pflow_mnl_estimation as mnl
from pflow_capacity_analysis import _iter_rows,third_mesh_code
from pflow_accessibility_estimation import mesh_center
from pflow_closure_config import excluded
from trip_chains import haversine_m
from pflow_origin_comparison import OUTSIDE_AICHI

SHARE=0.22389070848047118

def distances(lon,lat,points):
    """Haversine km; Java uses its own distance utility (pilot correction needed)."""
    points=np.asarray(points);x=np.radians(points[:,0]-lon);y=np.radians(points[:,1]-lat)
    a=np.sin(y/2)**2+np.cos(np.radians(lat))*np.cos(np.radians(points[:,1]))*np.sin(x/2)**2
    return 6371*2*np.arcsin(np.sqrt(np.clip(a,0,1)))

class ExpectedModel:
    def __init__(self,facilities,baseline,target,units,coefficients,indicator,sample_factor=50):
        self.target=target;self.units=units;self.c=coefficients;self.factor=sample_factor
        cities=mnl.load_cities(facilities,prefix='');params=mnl.load_params(facilities/'mnl')
        events=defaultdict(list)
        for p in sorted((baseline/'23').glob('*.csv')):
            previous=None
            for row in _iter_rows(p):
                before=previous if previous and previous['person_id']==row['person_id'] else None;previous=row
                if before and row['purpose']==100:
                    events[(before['gcode'],mnl.agent_type(row['labor']))].append((before['lon'],before['lat']))
        if not events:raise ValueError('No baseline shopping events')
        relevant=set(events)
        candidates={o:[d for d in cities if haversine_m(cities[o]['lon'],cities[o]['lat'],cities[d]['lon'],cities[d]['lat'])<=20000] for o,_ in relevant}
        codes=sorted(set().union(*candidates.values()));self.codes=codes;index={c:i for i,c in enumerate(codes)}
        names=sorted(set(units.unit_for_gcode(c) for c in codes));self.names=names;ui={u:i for i,u in enumerate(names)}
        memberships=defaultdict(set);capacity={};stores=defaultdict(Counter)
        # Read all economics: a shared mesh keeps the first valid-city value.
        with (facilities/'mesh_ecensus.csv').open() as f:
            for row in csv.DictReader(f):
                city=row['n03_007'];mesh=row['key_code']
                if city not in cities:continue
                capacity.setdefault(mesh,float(row['t000917029']))
                if city in index:memberships[city].add(mesh)
        with (facilities/'city_retail.csv').open() as f:
            next(f)
            for line in f:
                row=line.rstrip().split(',')
                if len(row)<=21 or row[7] not in cities or excluded(row):continue
                try:lon=float(row[20])-.000293;lat=float(row[21])+.000106950
                except ValueError:continue
                if not math.isfinite(lon+lat):continue
                mesh=third_mesh_code(lon,lat);stores[mesh][row[7]]+=1
                if row[7] in index:memberships[row[7]].add(mesh)
        capacity[target.meshCode]-=745.942954248366
        if capacity[target.meshCode]<0:raise ValueError('Negative closed mesh capacity')
        self.mesh_cap=capacity[target.meshCode]
        self.competing=stores[target.meshCode][target.gcode]*10000
        if self.competing<=0:raise ValueError('Expected model requires ordinary stores in target city/mesh')
        self.ti=index[target.gcode];self.nu=len(names);self.base_sum=np.zeros(len(codes));self.member=np.zeros(len(codes))
        self.numerator=np.zeros((len(codes),self.nu));self.delta=np.zeros_like(self.numerator);self.groups=[]
        self.facility_competing=np.full(len(codes),-1.)
        details={}
        for city in codes:
            i=index[city];meshes=sorted(memberships[city]);cap=np.array([capacity.get(m,0.) for m in meshes]);dist=np.zeros((len(meshes),self.nu))
            for j,mesh in enumerate(meshes):
                count=stores[mesh]
                if count.get(city):dist[j,ui[units.unit_for_gcode(city)]]=1
                elif count:
                    for code,n in count.items():dist[j,ui[units.unit_for_gcode(code)]]+=n/sum(count.values())
            ti=meshes.index(target.meshCode) if target.meshCode in meshes else None
            self.base_sum[i]=cap.sum();self.member[i]=ti is not None;self.numerator[i]=cap@dist
            if ti is not None:
                self.delta[i]=dist[ti]
                if city==target.gcode:self.facility_competing[i]=stores[target.meshCode][city]*10000
                elif not stores[target.meshCode].get(city):self.facility_competing[i]=sum(stores[target.meshCode].values())*10000
            details[city]=(meshes,cap,dist,ti)
        for (origin,kind),points in events.items():
            ds=candidates[origin];ids=np.array([index[d] for d in ds]);p=params[kind][cities[origin]['type']]
            deg=np.array([math.hypot(cities[d]['lon']-cities[origin]['lon'],cities[d]['lat']-cities[origin]['lat']) for d in ds])
            fixed=np.array([p[2]*(d==origin)+p[4]*cities[d]['area']*self.c['area_multiplier']+p[5]*cities[d]['pop_ratio']/1000*self.c['population_multiplier']+p[6]*cities[d]['office_ratio']/1000*self.c['office_multiplier'] for d in ds])
            meshes,cap,dist,ti=details[origin];centers=[mesh_center(m) for m in meshes]
            den=[];num=[];w=[]
            for lon,lat in points:
                weights=1/np.maximum(distances(lon,lat,centers),1e-9)**1.2
                weighted=weights*cap;den.append(weighted.sum());num.append(weighted@dist);w.append(weights[ti] if ti is not None else 0.)
            self.groups.append(dict(origin=origin,kind=kind,n=len(points),ids=ids,oi=index[origin],distance=p[0]*deg,fixed=fixed,
                                    den=np.array(den),num=np.array(num),w=np.array(w),origin_unit=units.unit_for_gcode(origin)))
        self.units_target=units.unit_for_gcode(target.gcode)
        self.indicator=np.array([indicator[c] for c in codes]);self.event_count=sum(g['n'] for g in self.groups)

    def predict(self,x):
        gamma_add,distance_scale,mesh_add,facility_cap=map(float,x)
        if min(gamma_add,mesh_add,facility_cap)<0 or distance_scale<=0 or not np.isfinite(x).all():raise ValueError('Invalid parameter vector')
        cap=self.base_sum+self.member*mesh_add
        share=np.where(self.facility_competing>=0,SHARE*facility_cap/np.maximum(self.facility_competing+facility_cap,1e-30),0.)
        destination_unit=self.names.index(self.units_target)
        new_dist=np.zeros(self.nu);new_dist[destination_unit]=1
        change=share[:,None]*(new_dist[None,:]-self.delta)
        target_dist=self.delta+change
        physical=np.divide(self.numerator+mesh_add*self.delta+(self.mesh_cap+mesh_add)*change,cap[:,None],out=np.zeros_like(self.numerator),where=cap[:,None]>0)
        ind=self.indicator.copy();ind[self.ti]+=gamma_add
        utility=self.c['beta']*np.log1p(cap)+self.c['gamma']*np.log1p(ind)
        origins=Counter();dest=np.zeros(self.nu);fail=0.
        for g in self.groups:
            v=g['fixed']+distance_scale*g['distance']+utility[g['ids']];v-=v.max();p=np.exp(v);p/=p.sum()
            n=g['n'];local=int(np.flatnonzero(g['ids']==g['oi'])[0]);den=g['den']+mesh_add*g['w']
            local_num=g['num']+g['w'][:,None]*(mesh_add*self.delta[g['oi']]+(self.mesh_cap+mesh_add)*change[g['oi']])
            local_physical=np.divide(local_num,den[:,None],out=np.zeros_like(local_num),where=den[:,None]>0).sum(axis=0)
            destination=n*(p@physical[g['ids']])+p[local]*(local_physical-n*physical[g['oi']])
            if g['origin_unit']!=OUTSIDE_AICHI:dest+=destination
            fail+=n-destination.sum()
            for pos,ci in enumerate(g['ids']):
                if share[ci]<=0:continue
                if ci==g['oi']:
                    mesh_weight=np.divide((self.mesh_cap+mesh_add)*g['w'],den,out=np.zeros_like(den),where=den>0).sum()
                else:mesh_weight=n*(self.mesh_cap+mesh_add)/cap[ci]
                origins[g['origin_unit']]+=p[pos]*mesh_weight*share[ci]*self.factor
        return dict(arrivals=sum(origins.values()),origins=dict(origins),destinations=dict(zip(self.names,(dest*self.factor).tolist())),expected_selection_failures=fail*self.factor)
