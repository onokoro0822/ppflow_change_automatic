"""Read-only report for the facility-specific A experiment."""
import csv,json,re,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pflow_scenario_metrics import references
from pflow_origin_comparison import OUTSIDE_AICHI

def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def status(metrics):
    result={}
    for cohort in ['Commuter','NonCommuter','Student']:
        path=Path(metrics['directory'])/'logs'/f'{cohort}.log'
        text=path.read_text()
        pattern=r'^\[\d+\] error:(\d+) total:(\d+)$' if cohort=='Student' else r'^\[\d+\]-(\d+)-(\d+)$'
        records=re.findall(pattern,text,re.M)
        if not records:raise ValueError('Missing status records: '+str(path))
        result[cohort]=dict(nonzero_return_persons=sum(int(a) for a,b in records),processed_persons=sum(int(b) for a,b in records),source=str(path))
    return result

def main():
    source=ROOT/'.local/pflow/output/independent_A_calibration_20261015'
    report=json.loads((source/'report.json').read_text())
    if report['status']!='finished':raise ValueError('Full calibration has not finished')
    old=json.loads((ROOT/'.local/pflow/output/gamma_A_52365790_20261014/report.json').read_text())
    oldcontrol=json.loads((ROOT/'.local/pflow/output/gamma_A_controls_20261015/controls.json').read_text())
    dest=ROOT/'docs/progress/figures/20261015_independent_A';dest.mkdir(parents=True,exist_ok=True)
    _,oref,_=references(ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_d5_shopping_facility_age.csv',ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_purpose_mode_raw.csv')
    save(dest/'summary.json',report)
    audit={name:status(r) for name,r in report['controls'].items()}
    lines=['## 確定した実験結果','','到着は延べ買物活動数。人数適合は外部から与えた需要への校正であり、需要予測ではない。前回と今回で固定係数も異なるため、差を専用重みだけの効果とは解釈しない。','','|方式・目標|到着|誤差%|出発地TVD|全県行先TVD|中村区買物|区外O→中村区|跡地既存店|通常買物→新施設|','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    origins=[];seeds=[];meshes=[];commands={}
    for goal,r in report['targets'].items():
        b=r['best'];audit[goal]=status(b)
        for name,value in [('前回A',old['targets'][goal]['best']),('専用重みA',b)]:
            c=value['counts'];n=c.get('facility',0)
            lines.append(f"|{name} {goal}|{n}|{100*(n/int(goal)-1):+.2f}|{value['origin_tvd']:.6f}|{value['destination_tvd']:.6f}|{c['target_city']}|{c['target_city_inflow']}|{c['target_mesh']-n}|{c.get('ordinary_facility_arrivals',0)}|")
        label=Path(b['directory']).parent.name
        commands[goal]=json.loads((source/label/'command.json').read_text())
        for name in ['scenario.json','gamma.json']:shutil.copyfile(source/label/name,dest/f't{goal}_{name}')
        denominator=sum(v for k,v in b['origin_counts'].items() if k!=OUTSIDE_AICHI)
        for unit in sorted(set(oref)|set(b['origin_counts'])):
            count=b['origin_counts'].get(unit,0)
            origins.append([goal,unit,count,count/denominator if unit!=OUTSIDE_AICHI and denominator else None,oref.get(unit,0) if unit!=OUTSIDE_AICHI else None])
        for row in r['final_seeds']:seeds.append([goal,row['input']['weight'],row['input']['seed'],row['counts'].get('facility',0),row['arrivals_full_equivalent'],row['origin_tvd'],row['destination_tvd']])
        for name,control in report['controls'].items():
            for mesh in sorted(set(b['mesh_counts'])|set(control['mesh_counts'])):
                meshes.append([goal,name,mesh,control['mesh_counts'].get(mesh,0),b['mesh_counts'].get(mesh,0),b['mesh_counts'].get(mesh,0)-control['mesh_counts'].get(mesh,0)])
    lines+=['','## 対照との差','','閉店後対照はγ19・mesh加算0。重み0対照はγ325・mesh加算745.942954248366を新設ケースとそろえたもの。前回Aは各目標の最終距離係数をそろえた閉店後対照。非来訪者の変更人数は活動系列が対応した人物の空間的な買物先変更で、系列不一致は別に記す。','','|目標・対照|中村区増減|区外Oからの増減|跡地既存店増減|非来訪者変更人数|非来訪者系列不一致|','|---|---:|---:|---:|---:|---:|']
    for goal,r in report['targets'].items():
        b=r['best'];c=b['counts']
        for name,control in report['controls'].items():
            v=control['counts'];paired=r['comparisons'][name]['counts']
            lines.append(f"|{goal}・{name}|{c['target_city']-v['target_city']:+d}|{c['target_city_inflow']-v['target_city_inflow']:+d}|{c['target_mesh']-c.get('facility',0)-v['target_mesh']:+d}|{paired.get('nonvisitor_changed_shopping_persons',0)}|{paired.get('unaligned_nonvisitor_persons',0)}|")
        prior=oldcontrol[goal]
        previous=old['targets'][goal]['best']['counts'];base=prior['postclosure_f1']['counts'];paired=prior['full_paired_comparison']['counts']
        lines.append(f"|{goal}・前回A閉店後対照|{previous['target_city']-base['target_city']:+d}|{previous['target_city_inflow']-base['target_city_inflow']:+d}|{previous['target_mesh']-previous.get('facility',0)-base['target_mesh']:+d}|{paired.get('nonvisitor_changed_shopping_persons',0)}|{paired.get('unaligned_nonvisitor_persons',0)}|")
        save(dest/f'previous_t{goal}_control.json',prior)
    lines+=['','## 最終重みと2%感度','','|目標|専用重み|全数±2%達成|seed42実件|seed43実件|seed44実件|seed45実件|','|---|---:|---|---:|---:|---:|---:|']
    for goal,r in report['targets'].items():lines.append('|'+goal+'|'+str(r['best']['input']['weight'])+'|'+str(r['count_met'])+'|'+'|'.join(str(x['counts'].get('facility',0)) for x in r['final_seeds'])+'|')
    lines+=['','各seedは2%の実件数、全数相当は50倍。全数で合わせた重みを共通使用しており、各seedを別個に校正したものではない。各seedのTVDは `figures/20261015_independent_A/seeds.csv`。','','## 出典と制約','','- 正確なコマンド・固定係数：`figures/20261015_independent_A/commands.json`。最終scenario/gammaも同ディレクトリに保存。','- 一次集計：`summary.json`、地区別PT比：`origins.csv`、メッシュ増減：`mesh_changes.csv`。','- Javaの選択失敗（非0戻り人数）は `generation_status.json`。例外なしを失敗0と呼ばない。','- PTは大型店買物、今回の候補は通常買物を含む。母集団の違いを残す参考適合である。','- 重みの調整は新施設に限定しているが、来訪後のO変更と活動生成失敗の変化は後続へ波及する。','- 既存行先間の条件付き確率比の不変性と、最終出力の非来訪者・既存店の絶対人数の不変性を区別する。']
    save(dest/'commands.json',commands);save(dest/'generation_status.json',audit)
    for name,header,rows in [('origins.csv',['target','origin','count','share','pt_share'],origins),('seeds.csv',['target','weight','seed','arrivals_sample','full_equivalent','origin_tvd','destination_tvd'],seeds),('mesh_changes.csv',['target','control','mesh','before','after','net'],meshes)]:
        with (dest/name).open('w') as f:
            writer=csv.writer(f);writer.writerow(header);writer.writerows(rows)
    path=ROOT/'docs/progress/20261015_新施設専用重み校正.md'
    base=path.read_text().split('## 確定した実験結果')[0].rstrip()
    path.write_text(base+'\n\n'+'\n'.join(lines)+'\n')
    print(json.dumps({g:dict(counts=r['best']['counts'],weight=r['best']['input']['weight'],count_met=r['count_met'],comparisons={k:v['counts'] for k,v in r['comparisons'].items()}) for g,r in report['targets'].items()},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
