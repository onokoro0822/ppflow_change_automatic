"""Build input-only Meitetsu closure overlays and audit their exact raw-row scope."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from pflow_capacity_analysis import third_mesh_code
PROJECT = Path(__file__).resolve().parent
ADDRESSES = ['愛知県　名古屋市中村区　名駅　１丁目　２−'+i for i in ['１','２','４']]

def excluded(row):
    return row[7] == '23105' and row[10].startswith('3402') and row[5] in ADDRESSES

def build(facilities, output):
    output.mkdir(parents=True, exist_ok=True)
    codes = {r['n03_007'] for r in csv.DictReader((facilities/'city_boundary.csv').open())}
    indicators = Counter(); addresses = Counter(); meshes = Counter(); rows = 0
    retail = facilities/'city_retail.csv'
    with retail.open() as f:
        next(f)
        for line in f:
            row = line.rstrip('\n').split(',')
            if len(row) <= 21: continue
            # Exclusions follow Java's raw comma split; indicator follows the adopted CSV parser.
            if excluded(row):
                rows += 1; addresses[row[5]] += 1
                meshes[third_mesh_code(float(row[20])-.000293, float(row[21])+.000106950)] += 1
    if rows != 306 or meshes != {'52365790':306}:
        raise ValueError(f'Closure scope changed: {rows}, {meshes}')
    with retail.open() as f:
        for row in csv.DictReader(f):
            if row['ADDCD'] in codes and row['BUSC'].startswith('3402'):
                indicators[row['ADDCD']] += 1
    source = PROJECT/'output/gamma_telepoint_3402/gamma_estimation.json'
    estimation = json.loads(source.read_text()); coefficients=estimation['transforms']['log1p_area']['models']['b']['coefficients']
    reference = {r['gcode']:float(r['indicator']) for r in csv.DictReader((PROJECT/'config/gamma/telepoint_3402_entries.csv').open())}
    if any(indicators[c] != n for c,n in reference.items()): raise ValueError('Indicator differs from adopted fit')
    for name,remove in [('preclosure',0),('postclosure',rows)]:
        values={c:float(indicators[c]) for c in sorted(codes)}; values['23105']-=remove
        (output/(name+'_b.json')).write_text(json.dumps(dict(gamma=coefficients['gamma'],transform='log1p',unit='TelePoint_BUSC3402_row_count',indicators=values),indent=2)+'\n')
    audit=dict(retail_file=str(retail.resolve()),retail_sha256=hashlib.sha256(retail.read_bytes()).hexdigest(),
               rows=rows,addresses=addresses,meshes=meshes,nakamura_before=indicators['23105'],nakamura_after=indicators['23105']-rows,
               capacity_removal_mesh_status='awaiting_user_confirmation_52365790_vs_52366700',
               mesh_capacity_remove=54374*65068/4743000,national_indicator_cities=len(codes),
               estimator_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),coefficients=coefficients,
               limitations=['Exact address match; floor suffixes are not removed.',
                 'BUSC3402 row count is a provisional proxy, not store count or floor area.',
                 'National rows use the same rule for border alternatives; coefficients were fitted on Aichi only.',
                 'Original estimation m2 metadata is inapplicable; its numeric log1p fit is retained.'])
    (output/'closure_input_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    return audit
if __name__=='__main__':
    print(json.dumps(build(PROJECT/'.local/pflow/data/facilities',PROJECT/'config/gamma'),ensure_ascii=False,indent=2))
