"""Bounded input-only A calibration: expected choices, pilots, full verification."""
import argparse,copy,json,os,pickle,subprocess,time
from pathlib import Path
import numpy as np
from pflow_gamma_estimation import nelder_mead
from pflow_origin_comparison import normalize,total_variation_distance,OUTSIDE_AICHI
from pflow_scenario_metrics import references,measure
from pflow_target import AnalysisTarget

ROOT=Path(__file__).resolve().parent
BOUNDS=np.array([[0.,3000.],[.3,1.5],[0.,60000.],[1000.,1e9]])

def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)

def objective(pred,target,origin_ref,dest_ref,w=.1,wd=1.):
    ot=total_variation_distance(normalize(pred['origins'],exclude=[OUTSIDE_AICHI]),origin_ref)
    dt=total_variation_distance(normalize(pred['destinations'],exclude=[OUTSIDE_AICHI]),dest_ref)
    return ((pred['arrivals']-target)/target)**2+w*ot+wd*dt,ot,dt

def optimize(model,target,origin_ref,dest_ref,scale=1.,w=.1,wd=1.):
    """Profile capacity by bisection so count has priority over distribution fit."""
    def evaluate(z,details=False):
        if np.any(z<0) or np.any(z>1):return 100+float(np.sum(np.maximum(-z,0)+np.maximum(z-1,0)))
        x=(BOUNDS[:3,0]+z*(BOUNDS[:3,1]-BOUNDS[:3,0])).tolist()
        lo,hi=BOUNDS[3];low=model.predict(x+[lo])['arrivals']*scale;high=model.predict(x+[hi])['arrivals']*scale
        if target<low or target>high:
            cap=lo if target<low else hi
            pred=model.predict(x+[cap]);pred['arrivals']*=scale
            val=objective(pred,target,origin_ref,dest_ref,w,wd)[0]+10*((pred['arrivals']-target)/target)**2
        else:
            competitors=model.facility_competing[model.facility_competing>=0]
            if np.all(competitors==competitors[0]):
                ceiling=high*(competitors[0]+hi)/hi
                cap=float(competitors[0]*target/(ceiling-target))
            else:
                for _ in range(24):
                    cap=(lo*hi)**.5
                    if model.predict(x+[cap])['arrivals']*scale<target:lo=cap
                    else:hi=cap
                cap=(lo*hi)**.5
            pred=model.predict(x+[cap]);pred['arrivals']*=scale
            val=objective(pred,target,origin_ref,dest_ref,w,wd)[0]
        if details:
            val,ot,dt=objective(pred,target,origin_ref,dest_ref,w,wd)
            return dict(parameters=x+[cap],predicted=pred,objective=val,origin_tvd=ot,destination_tvd=dt,count_scale=scale)
        return val
    starts=[[306/3000,(model.c['distance_multiplier']-.3)/1.2,745.942954248366/60000],[.5,.1,.5],[.95,.5,.95]]
    runs=[]
    for start in starts:
        z,meta=nelder_mead(evaluate,start,step=.08,max_iter=180,tol=2e-4)
        candidate=evaluate(z,True);candidate['search']=meta;runs.append(candidate)
    best=min(runs,key=lambda r:(abs(r['predicted']['arrivals']-target)/target>.02,r['objective']))
    best=copy.deepcopy(best);best['starts']=runs;return best

def options(c,gamma_path,distance):
    multipliers=','.join(map(str,[distance,c['area_multiplier'],c['population_multiplier'],c['office_multiplier']]))
    return (f'--shopping-attraction-beta {c["beta"]} --shopping-mnl-multipliers {multipliers} '
            f'--shopping-large-store 0.22389070848047118,{c["beta"]},{multipliers},1 '
            '--shopping-local-mesh-capacity raw --facility-city-match on --large-store-facility-only on '
            f'--activity-trace on --shopping-gamma-config {gamma_path}')

def run_case(out,label,x,seed,factor,c,target,units,oref,dref,full_home):
    case=out/label;case.mkdir(parents=True,exist_ok=False)
    scenario=json.loads((ROOT/'config/gamma/meitetsu_closed.json').read_text())
    scenario.update(scenarioId=label,injectFacility=True,meshCapacityAdd=x[2],facility=dict(id=target.facilityId,longitude=target.longitude,latitude=target.latitude,capacity=x[3]))
    gamma=json.loads((ROOT/'config/gamma/postclosure_b.json').read_text());gamma['indicators'][target.gcode]+=x[0]
    save(case/'scenario.json',scenario);save(case/'gamma.json',gamma)
    runroot=full_home/'output/gamma_A_52365790_20261014'/label if factor==1 else case/'generated'
    cmd=['bash',str(ROOT/'scripts/run_pflow_capacity_sweep.sh'),'--scenario-file',str(case/'scenario.json'),'--output-root',str(runroot),
         '--pflow-home',str(full_home if factor==1 else ROOT/'.local/pflow'),'--seed',str(seed),'--sample-factor',str(factor),'--skip-compile','--pflow-args',options(c,case/'gamma.json',x[1])]
    save(case/'command.json',dict(command=cmd,parameters=x,seed=seed,sample_factor=factor,analysis_target=target.as_dict()))
    env=dict(os.environ);env['PATH']='/Users/yuuki/tools/apache-maven-3.6.3/bin:'+env['PATH'];env['MAVEN_OPTS']='-Xmx6g'
    print('GENERATE',label,flush=True)
    with (case/'run.log').open('w') as log:subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    directory=runroot/'scenario'
    for p in (directory/'logs').glob('*.log'):
        text=p.read_text()
        if '[ERROR]' in text or 'Unresolved compilation' in text or 'Exception' in text:raise RuntimeError(f'Inspect generation errors: {p}')
    metrics=measure(directory,target,units,oref,dref)
    if metrics['counts'].get('ordinary_facility_arrivals',0):raise RuntimeError('Ordinary shopping reached new facility')
    metrics.update(seed=seed,sample_factor=factor,parameters=x,arrivals_full_equivalent=metrics['counts'].get('facility',0)*factor)
    save(case/'metrics.json',metrics);print('RESULT',label,metrics['counts'].get('facility',0),metrics['origin_tvd'],flush=True)
    return metrics

def main():
    p=argparse.ArgumentParser();p.add_argument('--model',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--full-home',type=Path,default=Path('/Volumes/PFLOW_SSD/PFLOW'));p.add_argument('--targets',type=int,nargs='+',default=[3000,6000]);p.add_argument('--pilot-rounds',type=int,default=2);p.add_argument('--full-rounds',type=int,default=3);p.add_argument('--w',type=float,default=.1);p.add_argument('--w-dest',type=float,default=1.);p.add_argument('--expectation-only',action='store_true');a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    with a.model.open('rb') as f:model=pickle.load(f) # locally produced cache, never external pickle
    c=model.c;target=AnalysisTarget.load(ROOT/'config/gamma/analysis_target.json')
    units,oref,dref=references(ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_d5_shopping_facility_age.csv',ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_purpose_mode_raw.csv')
    report=dict(status='running',method='input_only_A',bounds=BOUNDS.tolist(),parameter_order=['gamma_indicator_add_rows','distance_multiplier','meshCapacityAdd','facility_capacity'],w=a.w,w_destination=a.w_dest,count_tolerance=.02,analysis_target=target.as_dict(),targets={},notes=['Bounded search, not global optimum.','Facility capacity profiled to match expected count; no quotas or visitor reselection.','Scalar count correction only; no district coefficients.','PT is calibration input, not independent validation.'])
    save(out/'report.json',report)
    for goal in a.targets:
        record=dict(pilots=[],expectations=[],full=[]);report['targets'][str(goal)]=record;scale=1.
        for round_id in range(a.pilot_rounds):
            opt=optimize(model,goal,oref,dref,scale,a.w,a.w_dest);record['expectations'].append(opt);save(out/'report.json',report)
            print('OPTIMUM',goal,round_id,opt['parameters'],opt['origin_tvd'],flush=True)
            if a.expectation_only:break
            runs=[run_case(out,f't{goal}_r{round_id}_s{seed}',opt['parameters'],seed,50,c,target,units,oref,dref,a.full_home) for seed in range(42,46)]
            avg=sum(x['arrivals_full_equivalent'] for x in runs)/len(runs);record['pilots'].append(dict(round=round_id,mean=avg,runs=runs))
            scale=avg/max(model.predict(opt['parameters'])['arrivals'],1e-9)
            save(out/'report.json',report)
            if abs(avg-goal)/goal<=.02:break
        if not a.expectation_only:
            # A final scalar correction uses the pilot average; re-optimize deterministically.
            opt=optimize(model,goal,oref,dref,scale,a.w,a.w_dest);record['expectations'].append(opt)
            x=opt['parameters'];history=[]
            for j in range(a.full_rounds):
                result=run_case(out,f't{goal}_full{j}',x,42,1,c,target,units,oref,dref,a.full_home);record['full'].append(result);n=result['counts'].get('facility',0);history.append((x[3],n));save(out/'report.json',report)
                if abs(n-goal)/goal<=.02:break
                # Count feedback changes inputs and regenerates every activity.
                from pflow_joint_target_calibration import next_capacity
                cap=next_capacity([dict(capacity=f,arrivals_sample=count) for f,count in history],goal,model.competing)
                x=list(x);x[3]=float(np.clip(cap,*BOUNDS[3]))
            best=min(record['full'],key=lambda r:abs(r['counts'].get('facility',0)-goal));record['best']=best;record['count_met']=abs(best['counts'].get('facility',0)-goal)/goal<=.02
        save(out/'report.json',report)
    report['status']='expectation_only' if a.expectation_only else 'finished';save(out/'report.json',report)
if __name__=='__main__':main()
