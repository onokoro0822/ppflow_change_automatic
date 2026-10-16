"""Plot aggregate full experiment results; no activity edits."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1]
def main():
    dest=ROOT/'docs/progress/figures/20261015_independent_A'
    r=json.loads((dest/'summary.json').read_text())
    old=json.loads((ROOT/'.local/pflow/output/gamma_A_52365790_20261014/report.json').read_text())
    controls=json.loads((ROOT/'.local/pflow/output/gamma_A_controls_20261015/controls.json').read_text())
    goals=['3000','6000'];fig,axes=plt.subplots(2,2,figsize=(11,7.5),layout='constrained')
    labels=['3,000 target','6,000 target'];colors=['#a8a8a8','#167eaa']
    for index,method in enumerate(['previous','independent']):
        rows=[old['targets'][g]['best'] if method=='previous' else r['targets'][g]['best'] for g in goals]
        x=[i+(index-.5)*.32 for i in range(2)]
        vals=[v['counts']['facility'] for v in rows]
        bars=axes[0,0].bar(x,vals,.3,label=method,color=colors[index]);axes[0,0].bar_label(bars,padding=3,fontsize=9)
        axes[0,1].bar(x,[v['origin_tvd'] for v in rows],.3,label=method,color=colors[index])
        net=[];nonvisitor=[]
        for g,v in zip(goals,rows):
            base=controls[g]['postclosure_f1'] if method=='previous' else r['controls']['closed']
            net.append(v['counts']['target_mesh']-v['counts']['facility']-base['counts']['target_mesh'])
            pair=controls[g]['full_paired_comparison'] if method=='previous' else r['targets'][g]['comparisons']['closed']
            nonvisitor.append(pair['counts'].get('nonvisitor_changed_shopping_persons',0))
        for ax,values in [(axes[1,0],net),(axes[1,1],nonvisitor)]:
            bars=ax.bar(x,values,.3,label=method,color=colors[index]);ax.bar_label(bars,padding=3,fontsize=9)
    for i,g in enumerate(goals):axes[0,0].plot([i-.4,i+.4],[int(g)]*2,color='black',linestyle='--',linewidth=1)
    titles=['Full arrivals (dashed: target)','Origin TVD vs PT reference','Existing shops in target mesh: net arrivals','Nonvisitor destination changes: persons']
    for ax,title in zip(axes.flat,titles):
        ax.set_title(title,fontsize=11);ax.set_xticks([0,1],labels);ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);ax.margins(y=.16)
    axes[0,0].legend(frameon=False);axes[1,0].axhline(0,color='black',linewidth=.6)
    fig.suptitle('Dedicated facility weight vs previous A method — full seed 42',fontsize=14)
    fig.supxlabel('Bottom panels: change from each method’s matching post-closure baseline. Coefficients differ between methods.',fontsize=9)
    fig.savefig(dest/'comparison.png',dpi=180);plt.close(fig)
    print(dest/'comparison.png')
if __name__=='__main__':main()
