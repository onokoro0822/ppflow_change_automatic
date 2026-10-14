#!/usr/bin/env python3
"""Rebuild compact tables and figures from completed fixed-activity experiments."""
import argparse,csv,json,hashlib,shutil
from pathlib import Path
from collections import Counter
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import SymLogNorm
from pflow_capacity_analysis import third_mesh_code,TARGET_MESH,TARGET_GCODE

def load(p):return json.loads(p.read_text())
def equal_csv(a,b):
 files=sorted(a.glob('*.csv'));assert len(files)==207 and {p.name for p in files}=={p.name for p in b.glob('*.csv')}
 assert all(hashlib.sha256(p.read_bytes()).digest()==hashlib.sha256((b/p.name).read_bytes()).digest() for p in files)
 return len(files)
def center(code):
 s=str(code);return 100+int(s[2:4])+int(s[5])/8+int(s[7])/80+1/160,int(s[:2])*2/3+int(s[4])/12+int(s[6])/120+1/240

def main():
 p=argparse.ArgumentParser();p.add_argument('--local',type=Path,required=True);p.add_argument('--full',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
 full=load(a.full/'report.json');assert full['status']=='finished'
 results=[]
 for seed in [42,43,44,45]:
  r=load(a.local/f'seed{seed}_2pct_replay/report.json');assert r['status']=='finished'
  for run in r['runs']:results.append(dict(seed=seed,sample_factor=50,**{k:run[k] for k in ['target','actual_sample','tvd','quota_exact','audit','counts']}))
 for run in full['runs']:results.append(dict(seed=42,sample_factor=1,**{k:run[k] for k in ['target','actual_sample','tvd','quota_exact','audit','counts']}))
 checks={'default_csv_identical':equal_csv(a.local/'default/baseline/23',a.local.parents[0]/'capacity_experiment/baseline/23'),
         'target_zero_csv_identical':equal_csv(a.local/'seed42_2pct/baseline/23',a.local/'seed42_2pct_direct/target0/23'),
         'same_seed_csv_identical':equal_csv(a.local/'seed42_2pct_replay/target3000/23',a.local/'seed42_reprocheck/target3000/23')}
 direct=load(a.local/'seed42_2pct_direct/report.json')
 for run in direct['runs']:
  assert run['audit'].get('downstream_destination_changes',0)==0
  assert run['audit'].get('direct_destination_changes',0)==run['actual_sample']
 checks['direct_only_conservation']='passed: only facility-bound destinations changed'
 for run in full['runs']:
  mesh_arrivals=city_arrivals=facility_arrivals=0;failure=Counter();withdraw=Counter()
  with (Path(run['output'])/'audit.tsv').open() as f:
   for row in csv.DictReader(f,delimiter='\t'):
    if row['reason'] in ('direct_facility','repeat_facility'):
     facility_arrivals+=1;mesh_arrivals+=third_mesh_code(float(row['new_lon']),float(row['new_lat']))==TARGET_MESH;city_arrivals+=row['new_gcode']==TARGET_GCODE
     oldmesh=third_mesh_code(float(row['old_lon']),float(row['old_lat']))
     withdraw['target_mesh' if oldmesh==TARGET_MESH else ('other_mesh_target_city' if row['old_gcode']==TARGET_GCODE else 'outside_target_city')]+=1
    if row['reason']=='selection_failed_retained':failure[row['purpose']+':'+row['failure']]+=1
  assert facility_arrivals==mesh_arrivals==city_arrivals==run['target']
  checks[str(run['target'])]={'facility_mesh_city_gross_arrivals':[facility_arrivals,mesh_arrivals,city_arrivals],'withdrawals_by_output_city':dict(withdraw),'failures':dict(failure)}
  for name in ['mesh_effects.csv','city_effects.csv']:shutil.copyfile(Path(run['output'])/name,a.output/f"{run['target']}_{name}")
 with (a.output/'origin_composition.csv').open('w') as f:
  w=csv.writer(f);w.writerow(['origin_unit','pt_assumed_share','quota3000','actual3000','share3000','quota6000','actual6000','share6000'])
  for unit,share in full['pt_shares'].items():
   row=[unit,share]
   for run in full['runs']:row.extend([run['requested_quotas'][unit],run['actual_origins'].get(unit,0),run['actual_origins'].get(unit,0)/run['actual_sample']])
   w.writerow(row)
 with (a.output/'results.csv').open('w') as f:
  keys=['seed','sample_factor','target','actual_sample','tvd','quota_exact'];w=csv.writer(f);w.writerow(keys+['induced_persons','repeat_arrivals','downstream_changes','failures'])
  for r in results:w.writerow([r[k] for k in keys]+[r['counts'].get(k,0) for k in ['induced_persons','repeat_facility','downstream_reselected','selection_failures']])
 (a.output/'summary.json').write_text(json.dumps({'results':results,'checks':checks,'full_report':str(a.full/'report.json'),'baseline':full['baseline'],'pt_role':'assumed_input_not_validation'},ensure_ascii=False,indent=2))
 shutil.copyfile(a.local/'previous_diagnostics/report.json',a.output/'previous_diagnostics.json')
 # One-page model diagram: existing hierarchy and coupled intervention.
 fig,axs=plt.subplots(2,1,figsize=(13,8));fig.patch.set_facecolor('#f5f7fa')
 for ax in axs:ax.axis('off');ax.set_xlim(0,1);ax.set_ylim(0,1)
 axs[0].set_title('Baseline: city → mesh → facility',loc='left',fontsize=19,weight='bold')
 boxes=[(.02,'City (MNL)','P(c) ∝ exp(V(c))\nCommercial term in V(c)'),(.36,'Mesh conditional on city','Different city: P(m|c) ∝ E(m)\nSame city: P(m|c,O) ∝ E(m)/d^1.2'),(.72,'Facility in mesh','P(f|m,c) ∝ capacity(f)\nLarge-store filter + city match')]
 for x,title,body in boxes:axs[0].text(x,.63,title+'\n\n'+body,fontsize=11,va='center',bbox=dict(boxstyle='round,pad=.8',fc='white',ec='#b8c7d9'))
 for x in [.30,.67]:axs[0].annotate('',xy=(x+.04,.64),xytext=(x,.64),arrowprops=dict(arrowstyle='->',lw=2,color='#335c85'))
 axs[0].text(.02,.12,'Adopted baseline: raw retail-employment mesh weight E(m). Facility capacity acts only at the final selection.\nChanging commercial attraction can alter destinations of people who never visit the new facility.',fontsize=12)
 axs[1].set_title('Scenario: fixed activities + origin quotas + downstream destination replay',loc='left',fontsize=18,weight='bold')
 axs[1].text(.02,.74,'Recorded large-store activity\nactual preceding location O\nq = min(1, remaining quota / remaining candidates)',fontsize=12,bbox=dict(boxstyle='round,pad=.6',fc='white',ec='#b8c7d9'))
 axs[1].text(.59,.83,'Selected (q): new facility D\nD becomes the next O',fontsize=12,bbox=dict(boxstyle='round,pad=.6',fc='#dbeee8',ec='#39937c'))
 axs[1].text(.59,.47,'Not selected (1−q): retain baseline D\nAfter changed O only: replay free destinations',fontsize=12,bbox=dict(boxstyle='round,pad=.6',fc='white',ec='#b8c7d9'))
 for y in [.83,.48]:axs[1].annotate('',xy=(.57,y),xytext=(.49,.73),arrowprops=dict(arrowstyle='->',lw=2))
 axs[1].text(.02,.12,'New-facility probability is aggregated into its mesh and city.\nFix all times, purposes, home/work/school. Count repeat visits and final actual origins against quotas.\nPT composition and arrival totals are scenario assumptions; matching them is calibration, not demand validation.',fontsize=12)
 fig.tight_layout(pad=2);fig.savefig(a.output/'method.png',dpi=170);fig.savefig(a.output/'method.svg');plt.close(fig)
 fig,axs=plt.subplots(2,2,figsize=(12,10),constrained_layout=True)
 for i,run in enumerate(full['runs']):
  rows=list(csv.DictReader((Path(run['output'])/'mesh_effects.csv').open()))
  for j,effect in enumerate(['direct','downstream']):
   ax=axs[i,j];data=[r for r in rows if r['effect']==effect];xy=[center(r['mesh']) for r in data];v=[int(r['net_activity_arrivals']) for r in data];lim=max(map(abs,v),default=1)
   sc=ax.scatter([z[0] for z in xy],[z[1] for z in xy],c=v,cmap='RdBu_r',norm=SymLogNorm(linthresh=1,vmin=-lim,vmax=lim),marker='s',s=16)
   ax.scatter(*center(TARGET_MESH),marker='*',s=140,c='gold',edgecolors='black',zorder=4,label='New facility mesh');ax.set_title(f"{run['target']:,} arrivals | {effect}")
   if effect=='direct':
    net=sum(int(r['net_activity_arrivals']) for r in data if r['mesh']==TARGET_MESH)
    ax.annotate(f'Facility mesh: +{net:,}',xy=center(TARGET_MESH),xytext=(.03,.03),textcoords='axes fraction',fontsize=9,bbox=dict(fc='white',alpha=.9,ec='none'),arrowprops=dict(arrowstyle='->',color='black'))
   ax.set_xlim(136.55,137.30);ax.set_ylim(34.66,35.53);ax.set_xlabel('Longitude');ax.set_ylabel('Latitude');ax.set_aspect(1/0.82);ax.legend(fontsize=8)
   fig.colorbar(sc,ax=ax,label='Net activity arrivals / mesh (symlog)')
 fig.suptitle('Direct replacement and downstream changes — full population, seed 42\nAll activity purposes; separate color scales; no road-network assignment',fontsize=14);fig.savefig(a.output/'mesh_effects.png',dpi=170);fig.savefig(a.output/'mesh_effects.svg');plt.close(fig)
 print(json.dumps(checks,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
