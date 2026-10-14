"""Optional gamma-model estimation; requires an explicit, sourced facility indicator.
No activity CSV is modified. PT expanded counts give curvature-based pseudo-SEs,
not survey-design-adjusted standard errors.
"""
from __future__ import annotations
import csv,hashlib,json,math
from pathlib import Path
import numpy as np
from pflow_accessibility_estimation import AccessibilityModel


def load_indicators(path, codes):
    values={};sources={}
    with Path(path).open(newline='',encoding='utf-8-sig') as handle:
        reader=csv.DictReader(handle)
        if not {'gcode','indicator','source'}<=set(reader.fieldnames or []):
            raise ValueError('Indicator CSV requires gcode,indicator,source')
        for row in reader:
            code=row['gcode']
            if code in values:raise ValueError('Duplicate indicator city: '+code)
            value=float(row['indicator'])
            if not math.isfinite(value) or value<0 or not row['source'].strip():
                raise ValueError('Finite nonnegative indicator and source required')
            values[code]=value;sources[code]=row['source']
    missing=set(codes)-values.keys()
    if missing:raise ValueError('Explicit zero or value required for cities: '+','.join(sorted(missing)))
    vector=np.array([values[c] for c in codes])
    if np.ptp(vector)==0:raise ValueError('Constant indicator: gamma is not identifiable in MNL')
    return vector,sources


def area_feature(area, transform):
    area=np.asarray(area,dtype=float)
    if not np.isfinite(area).all() or np.any(area<0):raise ValueError('Invalid retail area')
    if transform=='log1p_area':return np.log1p(area)
    if transform=='presence':return (area>0).astype(float)
    raise ValueError('Unknown area transform')


def nelder_mead(fun,start,step=.2,max_iter=2500,tol=1e-7):
    x=np.asarray(start,dtype=float);n=len(x)
    simplex=np.vstack([x,x+np.eye(n)*step]);scores=np.array([fun(z) for z in simplex])
    converged=False
    for iteration in range(max_iter):
        order=np.argsort(scores);simplex=simplex[order];scores=scores[order]
        if np.max(np.abs(simplex[1:]-simplex[0]))<tol and np.ptp(scores)<tol:
            converged=True;break
        center=simplex[:-1].mean(axis=0);reflected=2*center-simplex[-1];fr=fun(reflected)
        if fr<scores[0]:
            expanded=center+2*(reflected-center);fe=fun(expanded)
            simplex[-1],scores[-1]=(expanded,fe) if fe<fr else (reflected,fr)
        elif fr<scores[-2]:simplex[-1],scores[-1]=reflected,fr
        else:
            outside=fr<scores[-1]
            contracted=center+.5*((reflected if outside else simplex[-1])-center);fc=fun(contracted)
            if fc<(fr if outside else scores[-1]):simplex[-1],scores[-1]=contracted,fc
            else:
                simplex[1:]=simplex[0]+.5*(simplex[1:]-simplex[0]);scores[1:]=[fun(z) for z in simplex[1:]]
    best=int(np.argmin(scores))
    return simplex[best],{'converged':converged,'iterations':iteration+1,'objective':float(scores[best])}


def numeric_hessian(fun,x,relative_step=1e-4):
    x=np.asarray(x,dtype=float);h=relative_step*np.maximum(1,np.abs(x));n=len(x);out=np.zeros((n,n));f=fun(x)
    for i in range(n):
        ei=np.eye(n)[i]*h[i];out[i,i]=(fun(x+ei)-2*f+fun(x-ei))/h[i]**2
        for j in range(i):
            ej=np.eye(n)[j]*h[j]
            out[i,j]=out[j,i]=(fun(x+ei+ej)-fun(x+ei-ej)-fun(x-ei+ej)+fun(x-ei-ej))/(4*h[i]*h[j])
    return out


class GammaModel(AccessibilityModel):
    def __init__(self,original,cities,params,indicator):
        super().__init__(original,cities,params);self.indicator=np.asarray(indicator)
    def probabilities(self,x,include_beta):
        gamma=x[0];beta=x[1] if include_beta else 0.;multipliers=x[2:] if include_beta else x[1:]
        v=self.fixed+np.einsum('i,ikod->kod',multipliers,self.basis)
        v+=gamma*self.indicator[None,None,:]+beta*self.original.ln_retail[None,None,:]
        v=np.where(self.original.available[None,:,:],v,-np.inf);v-=v.max(axis=2,keepdims=True)
        e=np.exp(v);city=np.sum(e/e.sum(axis=2,keepdims=True)*self.mixture.T[:,:,None],axis=0)
        num=self.membership.T@((city@self.membership)*self.origin_weights[:,None]);den=self.membership.T@self.origin_weights
        return np.divide(num,den[:,None],out=np.zeros_like(num),where=den[:,None]>0)


def conditional_score(prob,od,origins,destinations):
    allowed=np.asarray(destinations,dtype=bool)[None,:]&(prob>0)
    norm=np.where(allowed,prob,0).sum(axis=1)
    obs=np.asarray(origins,dtype=bool)[:,None]&allowed
    n=float(od[obs].sum())
    if n<=0:raise ValueError('No reachable PT trips in split')
    p=np.divide(prob,norm[:,None],out=np.zeros_like(prob),where=norm[:,None]>0)
    ll=float(np.sum(od[obs]*np.log(np.maximum(p[obs],1e-300))))
    total=float(od[np.asarray(origins)][:,np.asarray(destinations)].sum())
    return {'log_likelihood':ll,'trips':n,'mean_log_probability':ll/n,'excluded_unreachable_trips':total-n}


def fit_pair(model,od,holdout):
    holdout=np.asarray(holdout,dtype=bool);train=~holdout;all_units=np.ones(len(train),dtype=bool)
    if not holdout.any() or train.sum()<5:raise ValueError('Holdout and at least five training units required')
    result={}
    for name,include_beta in [('a',False),('b',True)]:
        names=['gamma']+(['beta'] if include_beta else [])+['distance_multiplier','area_multiplier','population_multiplier','office_multiplier']
        start=np.array([0.]*(2 if include_beta else 1)+[1.,1.,1.,1.]);distance_index=2 if include_beta else 1
        def loss(x):
            if not np.isfinite(x).all() or x[distance_index]<.05 or np.max(np.abs(x))>1e4:return 1e12
            return -conditional_score(model.probabilities(x,include_beta),od,train,train)['mean_log_probability']
        fits=[nelder_mead(loss,start+np.array([g]+[0.]*(len(start)-1))) for g in [0.,.5,-.5]]
        x,optimization=min(fits,key=lambda item:item[1]['objective'])
        prob=model.probabilities(x,include_beta);score=conditional_score(prob,od,train,train)
        h=numeric_hessian(loss,x)*score['trips'];eig=np.linalg.eigvalsh(h)
        identified=bool(eig.min()>max(1.,eig.max())*1e-9)
        se=np.sqrt(np.diag(np.linalg.inv(h))).tolist() if identified else [None]*len(x)
        # Holdout origin rows are entirely absent from training; evaluate all destinations.
        validation=conditional_score(prob,od,holdout,all_units)
        observed=od[holdout].sum(axis=0);predicted=(prob[holdout]*od[holdout].sum(axis=1)[:,None]).sum(axis=0)
        tvd=.5*float(np.abs(observed/observed.sum()-predicted/predicted.sum()).sum())
        result[name]={'coefficients':dict(zip(names,map(float,x))),'standard_errors':dict(zip(names,se)),
                      'standard_error_type':'numerical_curvature_expanded_PT_counts_not_survey_robust',
                      'hessian_eigenvalues':eig.tolist(),'identified':identified,'optimization':optimization,
                      'train':score,'aic':2*len(x)-2*score['log_likelihood'],
                      'holdout_origin_all_destinations':{**validation,'destination_tvd':tvd},
                      'holdout_destination_from_train_origins':conditional_score(prob,od,train,holdout)}
    delta=result['a']['aic']-result['b']['aic']
    # AIC rule is reported, never silently adopted. Invalid fits get no recommendation.
    usable=all(r['identified'] and r['optimization']['converged'] for r in result.values())
    return {'models':result,'aic_improvement_by_beta':delta,'aic_small_improvement_threshold':2.,
            'recommendation':('b' if delta>2 else 'a') if usable else None,
            'adopted_model':None,'status':'awaiting_user_model_confirmation' if usable else 'fit_requires_review'}


def run(args):
    import pflow_mnl_estimation as est
    import chukyo_pt_origin_distribution as pt
    from pflow_origin_comparison import ComparisonUnits
    output=args.output_dir/'gamma_estimation.json'
    if output.exists():raise ValueError('Use a fresh output directory')
    facilities=args.pflow_home/'data/facilities';cities=est.load_cities(facilities)
    for city in cities.values():city['ln_retail']=city[args.attraction]
    params=est.load_params(facilities/'mnl');baseline=args.baseline_dir or args.pflow_home/'output/capacity_experiment/baseline/23'
    units=ComparisonUnits(pt.load_middle_zone_labels(args.zone_xlsx));original=est.DestinationModel(cities,params,est.chooser_weights(baseline),units)
    indicator,sources=load_indicators(args.gamma_indicators,original.codes)
    if not args.gamma_holdout_gcodes:raise ValueError('Specify existing-large-facility holdout city codes before fitting')
    held=set(args.gamma_holdout_gcodes.split(','))
    if not held<=set(original.codes) or any(indicator[original.index[c]]<=0 for c in held):raise ValueError('Holdout cities must have positive documented indicators')
    held_units={units.unit_for_gcode(c) for c in held};mask=np.array([u in held_units for u in original.unit_names])
    od=est.pt_unit_od(args.pt_csv,units,original.unit_names)
    transforms=['log1p_area','presence'] if args.gamma_transform=='both' else [args.gamma_transform]
    fits={}
    for transform in transforms:
        feature=area_feature(indicator,transform)
        if np.ptp(feature)==0:
            fits[transform]={'status':'unidentifiable_constant_indicator','adopted_model':None}
        else:
            fits[transform]=fit_pair(GammaModel(original,cities,params,feature),od,mask)
    report={'transforms':fits,'adopted_model':None,'status':'awaiting_user_model_confirmation',
            'new_facility_area_m2':54374,'indicator_input_unit':'m2',
            'lab_coefficients_by_agent_and_city_type':params}
    target=original.index.get('23105')
    if target is not None:
        report['nakamura_feature_increment_54374m2']={t:float(area_feature(indicator[target]+54374,t)-area_feature(indicator[target],t)) for t in transforms}
    report.update(indicator_file=str(args.gamma_indicators.resolve()),indicator_sha256=hashlib.sha256(args.gamma_indicators.read_bytes()).hexdigest(),
                  indicator_sources=sources,holdout_units=sorted(held_units),holdout_gcodes=sorted(held),
                  pt_file=str(args.pt_csv.resolve()),pt_sha256=hashlib.sha256(args.pt_csv.read_bytes()).hexdigest(),
                  attraction=args.attraction,training='both origin and destination holdout units excluded; conditional training choice set',
                  limitations=['aggregate city-choice surrogate; not facility validation','PT expanded-count AIC and curvature SE, not survey-design inference','20 km unreachable observations excluded and reported'])
    args.output_dir.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0
