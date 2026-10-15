"""Read saved A metrics and build reviewable tables/figures; never edit activities."""
import csv,json,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pflow_scenario_metrics import references
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def main():
    source=ROOT/'.local/pflow/output/gamma_A_52365790_20261014'
    control=ROOT/'.local/pflow/output/gamma_A_controls_20261015/controls.json'
    report=json.loads((source/'report.json').read_text())
    controls=json.loads(control.read_text()) if control.exists() else {}
    reaudit_path=control.parent/'full_reaudit.json'
    reaudit=json.loads(reaudit_path.read_text()) if reaudit_path.exists() else {}
    dest=ROOT/'docs/progress/figures/20261015_gamma_A';dest.mkdir(parents=True,exist_ok=True)
    estimation=json.loads((ROOT/'output/gamma_telepoint_3402/gamma_estimation.json').read_text())
    _,oref,_=references(ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_d5_shopping_facility_age.csv',ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_purpose_mode_raw.csv')
    output=dict(source=str(source/'report.json'),controls_source=str(control),status=report['status'],parameter_order=report['parameter_order'],bounds=report['bounds'],weights=dict(origin=report['w'],destination=report['w_destination']),targets={},controls=controls,estimation=estimation['transforms'],notes=report['notes'])
    lines=['# 主方式A：閉店後基準の自動校正（2026-10-15）','','入力と係数だけを変更してJavaを再実行した。生成後CSVの書換え・地区枠・来訪者の選び直しは行っていない。人数は設定した±2%を満たすが、PT出発地分布には大きなずれが残る。大域最適・実施設需要の予測とは主張しない。','','## 全数seed42の確認結果','','|目標|実到着|人数誤差|出発地TVD|愛知県内ODの行先TVD|中村区買物|対象メッシュ買物|通常買物→新施設|','|---|---|---|---|---|---|---|---|']
    params=[];origin_rows=[];pilot_rows=[]
    for goal,record in report['targets'].items():
        b=record['best'];n=b['counts'];g=int(goal)
        lines.append(f"|{g:,}|{n['facility']:,}|{(n['facility']/g-1)*100:+.2f}%|{b['origin_tvd']:.6f}|{b['destination_tvd']:.6f}|{n['target_city']:,}|{n['target_mesh']:,}|{n.get('ordinary_facility_arrivals',0)}|")
        cmd=json.loads((source/Path(b['directory']).parent.name/'command.json').read_text())
        for filename in ['scenario.json','gamma.json']:
            shutil.copyfile(source/Path(b['directory']).parent.name/filename,dest/f't{goal}_{filename}')
        output['targets'][goal]=dict(best=b,count_met=record['count_met'],pilots=[dict(round=p['round'],mean=p['mean'],runs=[{k:v for k,v in r.items() if k not in ('mesh_counts','city_counts','destination_counts','origin_counts')} for r in p['runs']]) for p in record['pilots']],command=cmd)
        params.append([g]+b['parameters'])
        den=sum(v for k,v in b['origin_counts'].items() if k != '愛知県外・不明')
        for unit in sorted(set(oref)|set(b['origin_counts'])):origin_rows.append([g,unit,b['origin_counts'].get(unit,0),b['origin_counts'].get(unit,0)/den if unit != '愛知県外・不明' else None,(oref.get(unit,0.) if unit != '愛知県外・不明' else None)])
        for p in record['pilots']:
            for r in p['runs']:pilot_rows.append([g,p['round'],r['seed'],r['counts']['facility'],r['arrivals_full_equivalent'],r['origin_tvd'],r['destination_tvd']])
    lines+=['','### 2%のseed感度（最終pilot回、校正途中の入力）','','|目標|seed42|seed43|seed44|seed45|平均の全数換算|','|---|---|---|---|---|---|']
    for goal,record in report['targets'].items():
        pilot=record['pilots'][-1]
        lines.append('|'+goal+'|'+'|'.join(str(r['counts']['facility']) for r in pilot['runs'])+f"|{pilot['mean']}|")
    lines+=['','seed列は2%の実到着数。全seedが人数±2%に入ったという意味ではない。この後に全数の人数フィードバックで入力を更新しており、最終入力の全数seed43〜45感度は未検証。']
    lines+=['','到着数は延べ買物活動数で、ユニーク人数ではない。施設IDで識別し、同じ座標の残存店舗を含めない。PTは中村区着・大型規模小売店・買物の直前活動地点、愛知県内条件付き。行先TVDもO・Dとも愛知県内の買物に限定する。6,000側の県外・不明Oが1件あり、条件付きTVDの分母から除く。','','## 最終入力','','|目標|γ指標加算（3402行数相当）|距離倍率|meshCapacityAdd|施設capacity|','|---|---|---|---|---|']
    for p in params:lines.append('|'+ '|'.join(str(x) for x in p)+'|')
    lines+=['','出発地残差の例（愛知県内構成比）：3,000側の中川区は28.29%（PT3.35%）、中村区は22.17%（PT36.36%）、港区は10.26%（PT1.24%）。地区別全表は `figures/20261015_gamma_A/origins.csv`。共有メッシュの市区町村間フォールバックや区内メッシュ距離重みの寄与は【仮】で、原因別寄与をまだ分離していない。']

    lines+=['','目的関数は人数相対誤差² + **0.1 × 出発地TVD + 1.0 × 全県行先TVD**。期待人数を合わせる施設capacityを内側で解き、外側の3変数を有界Nelder–Meadで探索する。候補不足時には人数ペナルティを加える。2% seed42〜45を2回、全数seed42を2回実行。全数の人数フィードバックも入力変更と再生成で行った。地区別補正はない。','', '施設capacityは上限10億付近。meshCapacityAddも閉店時減算745.943に比べ大きく、校正値を実際の従業者数・床面積と解釈しない。3,000と6,000でγ指標加算が大きく異なり、単一の実店舗規模を同定できたとはいえない。','','## 係数推定と採用','','本人決定でln(1+3402行数)の(b)を採用。元JSONの`log1p_area`というキー名は面積を意味しない。54,374㎡をこの指標へ直接加算していない。','','|項|a推定値（数値標準誤差）|b推定値（数値標準誤差）|','|---|---|---|']
    models=estimation['transforms']['log1p_area']['models']
    for key in ['gamma','beta','distance_multiplier','area_multiplier','population_multiplier','office_multiplier']:
        cols=[]
        for name in ['a','b']:
            m=models[name];cols.append(f"{m['coefficients'][key]:.9f} ({m['standard_errors'][key]:.9f})" if key in m['coefficients'] else '—')
        lines.append('|'+key+'|'+'|'.join(cols)+'|')
    lines+=['','|指標|モデル|AIC|ホールドアウト行先TVD|','|---|---|---|---|']
    for transform,value in estimation['transforms'].items():
        for model,m in value['models'].items():lines.append(f"|{transform}|{model}|{m['aic']:.3f}|{m['holdout_origin_all_destinations']['destination_tvd']:.6f}|")
    for name,m in models.items():lines.append(f"\n{name}: 対数尤度 {m['train']['log_likelihood']:.6f}、AIC {m['aic']:.6f}、ホールドアウト出発地からの行先TVD {m['holdout_origin_all_destinations']['destination_tvd']:.6f}。")
    lines+=['','標準誤差はPT拡大集計値の数値曲率であり、調査設計を考慮した標準誤差ではない。ホールドアウトは一宮市発の都市選択適合で、既存大型施設の来訪者構成を独立検証した結果ではない。','','## 閉店・新設による変化','','閉店前と閉店後、新設ありで同じ距離倍率を用いる比較を別に生成する。距離倍率0.917の既存baselineと0.690の新設結果をそのまま施設効果として差し引かない。']
    if controls:
        lines+=['','|目標設定|状態|標本倍率|中村区買物|区外Oから中村区|対象メッシュ|行先TVD|','|---|---|---|---|---|---|---|']
        for g,values in controls.items():
            for state,m in values.items():
                if 'counts' not in m or 'directory' not in m:continue
                c=m['counts'];lines.append(f"|{g}|{state}|{'50' if state.endswith('50') else '1'}|{c.get('target_city',0)}|{c.get('target_city_inflow',0)}|{c.get('target_mesh',0)}|{m['destination_tvd']:.6f}|")
            for key in ['closure_paired_comparison','full_paired_comparison']:
                if key in values:lines.append('\n'+g+' '+key+': `'+json.dumps(values[key]['counts'],ensure_ascii=False)+'`')
    if reaudit:
        output['full_reaudit']=reaudit
        lines+=['','SSD再接続後、全数の新施設ありCSVを再集計した。確認済みケース: '+', '.join(g for g,r in reaudit.items() if r['count_fields_identical'])+'。施設・出発地・行先・都市・メッシュ件数が保存済み集計と一致。']
    full_controls={g:v for g,v in controls.items() if 'postclosure_f1' in v and 'full_paired_comparison' in v}
    if full_controls:
        lines+=['','### 距離係数をそろえた全数比較（閉店後→新施設あり、seed42）','','|設定|指標|閉店後|新施設あり|増減|','|---|---|---|---|---|']
        for g,v in full_controls.items():
            before=v['postclosure_f1'];after=report['targets'][g]['best']
            for key,label in [('facility','新施設'),('target_mesh','対象メッシュ全店'),('target_city','中村区全体'),('target_city_inflow','区外Oから中村区'),('shopping_total','出力全体の買物')]:
                a=before['counts'].get(key,0);b=after['counts'].get(key,0)
                lines.append(f'|{g}|{label}|{a:,}|{b:,}|{b-a:+,}|')
            a=before['counts'].get('target_mesh',0)-before['counts'].get('facility',0);b=after['counts'].get('target_mesh',0)-after['counts'].get('facility',0)
            lines.append(f'|{g}|対象メッシュの既存店|{a:,}|{b:,}|{b-a:+,}|')
            a=before['destination_tvd'];b=after['destination_tvd']
            lines.append(f'|{g}|愛知県内OD行先TVD|{a:.6f}|{b:.6f}|{b-a:+.6f}|')
        lines+=['','対象メッシュと中村区は重なる集計で、加算して総効果にしない。全店到着の増減であり、直接転換と来訪後の連鎖効果を因果的に分解した値ではない。閉店前→後は上の2%比較、新施設追加はこの全数比較として区別する。','','|設定|非来訪者の買物先変更（活動件数）|同（人数）|全体の活動系列不一致人数|うち非来訪者|','|---|---|---|---|---|']
        for g,v in full_controls.items():
            c=v['full_paired_comparison']['counts']
            lines.append('|'+g+'|'+'|'.join(f"{c.get(k,0):,}" for k in ['nonvisitor_changed_shopping_events','nonvisitor_changed_shopping_persons','unaligned_persons','unaligned_nonvisitor_persons'])+'|')
        lines+=['','非来訪者は新施設あり出力で新施設IDへの買物到着がない人。日時・目的等が対応する人だけで行き先変更を数え、不一致の系列は別計上する。同座標・同所在地で施設IDだけ変わる場合は、空間的な行き先変更に含めない。','','![閉店後から新施設ありのメッシュ別買物到着増減](figures/20261015_gamma_A/mesh_changes.png)']
    if full_controls:
        lines+=['','### 既存店も増える構造','','市区町村・メッシュの魅力度は通常買物にも作用する。一方、新施設への選択資格は大型店買物分岐（確率0.2238907085）に限定される。今回の最終設定で新施設到着／対象メッシュ全到着は3,000側22.75%、6,000側22.67%。施設capacityを上限付近まで増やすと、大型店分岐の施設選択はほぼ飽和しても、通常買物は同メッシュの既存店へ向かう。これは新施設だけへ任意人数を増やす操作ではない。','', '近似モデルの対象メッシュ既存競合capacityは2,570,000、新施設capacityは約10億で、対象の候補pool内のcapacity比は約99.74%。全経路の選択確率や利用率と同一視しない。市区町村選択・共有メッシュ・移動連鎖も介在するため、上の22.4%から全数を厳密に逆算できるとは主張しない。','','```mermaid','flowchart LR','    A[買物活動] --> B[市区町村MNL: β・γ・距離]','    B --> C[メッシュ選択: 追加capacityが両分岐に作用]','    C --> D[大型店買物: 約22.4%]','    C --> E[通常買物: 約77.6%]','    D --> F[新施設または既存店: capacity比]','    E --> G[既存店のみ]','```','', '今後は「到着数を満たす入力があるか」と「周辺の変化が妥当か」を別々に評価する。大型店限定のフィルターを緩めたり、地区枠を割り当てたりして、この差を隠さない。']
    status_path=dest/'generation_status.json'
    if status_path.exists():
        statuses=json.loads(status_path.read_text());output['generation_status']=statuses
        lines+=['','### 既存の活動生成失敗と例外の区別','','|設定|状態|就業者の非0戻り|非就業者の非0戻り|学生の非0戻り|','|---|---|---|---|---|']
        for g,v in statuses.items():
            for state,cohorts in v.items():lines.append('|'+g+'|'+state+'|'+'|'.join(f"{cohorts[c]['nonzero_return_persons']:,}" for c in ['Commuter','NonCommuter','Student'])+'|')
        lines+=['','`Commuter/NonCommuter/Student` のタスク集計ログを読み、createActivities/createActivityの非0戻りを数えた。行き先選択がnullになると活動を消して自宅のみへ戻す既存処理（return 3）があり、Aでは保持されている。今回新設した処理ではなく閉店後baselineにも存在するが、活動系列の保全を満たしたとは報告しない。例外・コンパイル失敗がないこと、非0戻り件数、前後の系列不一致人数は異なる指標。失敗の人物ID・目的別内訳はこのログでは識別できず、原因別監査は残課題。出典 `generation_status.json` と `scripts/audit_gamma_generation_status.py`。']
    status='全数CSVはSSDに保存。係数一致baselineと非来訪者変更監査が両目標で完了。' if len(full_controls)==len(report['targets']) else '全数の係数一致baseline生成と非来訪者監査は継続中。未完了のケースを確認済みとして扱わない。'
    lines+=['',status,'','## 活動固定Bとの比較','','|方式|3,000目標の実数/TVD|6,000目標の実数/TVD|解釈|','|---|---|---|---|','|A（今回）|3,040 / 0.458750|5,991 / 0.458073|入力を変えて活動生成し直す|','|B（保存済み旧実験）|3,000 / 0.001510|6,000 / 0.000797|地区別整数枠への割当て|','','Bは閉店前・旧メッシュ52366700の別条件。方式の性質を示す参考比較であり、同条件の優劣比較ではない。Bの小さなTVDは入力枠への適合で、予測精度を意味しない。出典 `figures/20261014_fixed_activity/results.csv`。','','## 再実行・出力先','','最終scenario/gamma設定は `figures/20261015_gamma_A/t3000_{scenario,gamma}.json` および `t6000_{scenario,gamma}.json` に複製保存した。個別の実行コマンドは`figures/20261015_gamma_A/summary.json`の`targets.*.command.command`、元ファイルは `.local/pflow/output/gamma_A_52365790_20261014/t3000_full1/command.json` と `t6000_full1/command.json`。全数CSVは `/Volumes/PFLOW_SSD/PFLOW/output/gamma_A_52365790_20261014/t{3000,6000}_full1/scenario/23`。','','```sh','.venv/bin/python pflow_gamma_controls.py --report .local/pflow/output/gamma_A_52365790_20261014/report.json --output .local/pflow/output/gamma_A_controls_20261015 --resume','.venv/bin/python scripts/report_gamma_a.py','```','','最終パラメータでのv1→v2感度も確認した（補正前期待値：3,031.875→3,030.650、5,953.208→5,950.822）。出典 `figures/20261015_gamma_A/final_parameter_surrogate_sensitivity.json`。この差だけで出発地TVDの大きな残差を説明することはできない。近似モデルv1では病院・飲食店由来の一部共有メッシュ所属が欠けた。v2修正で初回3,000候補の期待数は2,999.978→2,998.765（約−0.04%）。今回の探索はv1、最終人数はJava実生成の値。近似はbaselineのO・活動を固定するため、連鎖的変化は実生成でしか評価できない。','','![人数・適合度](figures/20261015_gamma_A/calibration.png)\n\n![PTと実生成の直前活動地区構成](figures/20261015_gamma_A/origin_composition.png)','','全数seed感度、同条件B比較、既存施設による独立検証、床面積からの需要予測は未完了。']
    for name,header,rows in [('parameters.csv',['target']+report['parameter_order'],params),('origins.csv',['target','origin','arrivals','observed_share_aichi','pt_share_aichi'],origin_rows),('pilots.csv',['target','round','seed','arrivals_sample','full_equivalent','origin_tvd','destination_tvd'],pilot_rows)]:
        with (dest/name).open('w') as f:w=csv.writer(f,lineterminator="\n");w.writerow(header);w.writerows(rows)
    (dest/'summary.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'docs/progress/20261015_A方式全数校正.md').write_text('\n'.join(lines)+'\n')
    fig,axs=plt.subplots(1,2,figsize=(10,3.8),layout='constrained');goals=list(report['targets']);actual=[report['targets'][g]['best']['counts']['facility'] for g in goals]
    axs[0].bar(goals,actual,color=['#147D92','#F19B38']);axs[0].scatter(goals,list(map(int,goals)),color='black',marker='_',s=600,label='Target');axs[0].set(title='Full population, seed 42',ylabel='Shopping arrivals');axs[0].legend()
    for g,n in zip(goals,actual):axs[0].text(g,n+100,f'{n:,}',ha='center')
    x=[0,1];axs[1].bar([i-.18 for i in x],[report['targets'][g]['best']['origin_tvd'] for g in goals],width=.36,label='Facility origin TVD');axs[1].bar([i+.18 for i in x],[report['targets'][g]['best']['destination_tvd'] for g in goals],width=.36,label='Aichi destination TVD');axs[1].set(xticks=x,xticklabels=goals,ylim=(0,.6),title='Calibration fit (not validation)',ylabel='TVD');axs[1].legend()
    fig.savefig(dest/'calibration.png',dpi=170);plt.close(fig)
    districts=['名古屋市中村区','名古屋市中川区','名古屋市港区','名古屋市中区']
    labels=['Nakamura','Nakagawa','Minato','Naka','Other Aichi']
    series={'PT reference':[oref.get(k,0.) for k in districts]}
    for goal,record in report['targets'].items():
        counts=record['best']['origin_counts'];den=sum(v for k,v in counts.items() if k!='愛知県外・不明')
        series['A '+goal]=[counts.get(k,0)/den for k in districts]
    fig,ax=plt.subplots(figsize=(8,3.8),layout='constrained')
    for i,(name,values) in enumerate(series.items()):
        values=values+[1-sum(values)]
        ax.bar([j+(i-1)*.25 for j in range(5)],[v*100 for v in values],width=.25,label=name)
    ax.set(xticks=range(5),xticklabels=labels,ylabel='Share (%)',title='Previous activity district (Aichi-only)',ylim=(0,55));ax.legend()
    fig.savefig(dest/'origin_composition.png',dpi=170);plt.close(fig)

    if full_controls:
        from matplotlib.colors import SymLogNorm
        from matplotlib.ticker import MaxNLocator
        import math
        fig,axes=plt.subplots(1,len(full_controls),figsize=(6*len(full_controls),4.8),layout='constrained',squeeze=False)
        mesh_rows=[]
        maxabs=max(1,max(abs(report['targets'][g]['best']['mesh_counts'].get(m,0)-v['postclosure_f1']['mesh_counts'].get(m,0)) for g,v in full_controls.items() for m in set(report['targets'][g]['best']['mesh_counts'])|set(v['postclosure_f1']['mesh_counts'])))
        for ax,(g,v) in zip(axes[0],full_controls.items()):
            before=v['postclosure_f1']['mesh_counts'];after=report['targets'][g]['best']['mesh_counts']
            coords=[]
            for mesh in sorted(set(before)|set(after)):
                delta=after.get(mesh,0)-before.get(mesh,0)
                mesh_rows.append([g,mesh,before.get(mesh,0),after.get(mesh,0),delta])
                lon=100+int(mesh[2:4])+int(mesh[5])/8+(int(mesh[7])+.5)/80
                lat=int(mesh[:2])*2/3+int(mesh[4])/12+(int(mesh[6])+.5)/120
                if 136.7<=lon<=137.1 and 35.04<=lat<=35.32:coords.append((lon,lat,delta))
            plot=ax.scatter([x[0] for x in coords],[x[1] for x in coords],c=[x[2] for x in coords],s=32,marker='s',cmap='RdBu_r',norm=SymLogNorm(linthresh=10,vmin=-maxabs,vmax=maxabs))
            target=report['analysis_target'];ax.scatter([target['longitude']],[target['latitude']],marker='*',facecolors='none',edgecolors='black',s=200)
            ax.set(title=f'Target {g}: new - closed (seed 42)',xlabel='Longitude',ylabel='Latitude',xlim=(136.7,137.1),ylim=(35.04,35.32));ax.set_aspect(1/math.cos(math.radians(35.2)))
            ax.xaxis.set_major_locator(MaxNLocator(5))
            fig.colorbar(plot,ax=ax,label='Shopping arrivals / day (symlog)')
        fig.savefig(dest/'mesh_changes.png',dpi=170);plt.close(fig)
        with (dest/'mesh_changes.csv').open('w') as f:
            w=csv.writer(f,lineterminator="\n");w.writerow(['target','mesh','closed_arrivals','new_arrivals','net_change']);w.writerows(mesh_rows)
    print(dest)
if __name__=='__main__':main()
