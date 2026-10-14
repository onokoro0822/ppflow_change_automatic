"""Read-only scenario metrics with explicit facility ID and actual preceding O."""
from collections import Counter
from pathlib import Path
import pflow_origin_comparison as oc
import pflow_mnl_estimation as mnl
from pflow_capacity_analysis import third_mesh_code

def references(pt_facility,pt_all):
    labels=oc.pt.load_middle_zone_labels(oc.pt.DEFAULT_ZONE_CODE_XLSX);units=oc.ComparisonUnits(labels)
    names=sorted(set(units.unit_for_gcode(c) for c in oc.AICHI_MUNICIPALITIES))
    od=mnl.pt_unit_od(pt_all,units,names)
    return units,oc.normalize(oc.pt_origin_counts(pt_facility,units,labels)['pt_shopping'],exclude=[oc.OUTSIDE_AICHI]),oc.normalize(dict(zip(names,od.sum(axis=0))))

def measure(directory,target,units,origin_ref,dest_ref):
    counts=Counter();origins=Counter();destinations=Counter();meshes=Counter();city=Counter()
    paths=sorted((Path(directory)/'23').glob('*.csv'))
    if not paths:raise FileNotFoundError(directory)
    for p in paths:
        previous=None
        for row,trace in target.rows(p):
            before=previous if previous and previous['person_id']==row['person_id'] else None;previous=row
            if row['purpose']!=100:continue
            counts['shopping_total']+=1;city[row['gcode']]+=1
            mesh=third_mesh_code(row['lon'],row['lat']);meshes[mesh]+=1
            if mesh==target.meshCode:counts['target_mesh']+=1
            if row['gcode']==target.gcode:
                counts['target_city']+=1
                if before and before['gcode']!=target.gcode:counts['target_city_inflow']+=1
            if before:
                ou=units.unit_for_gcode(before['gcode']);du=units.unit_for_gcode(row['gcode'])
                if oc.OUTSIDE_AICHI not in (ou,du):destinations[du]+=1
            if target.matches(row,trace):
                counts['facility']+=1
                if trace['large_store']!='1':counts['ordinary_facility_arrivals']+=1
                if before:origins[units.unit_for_gcode(before['gcode'])]+=1
    normal=oc.normalize(origins,exclude=[oc.OUTSIDE_AICHI]);dn=oc.normalize(destinations)
    return dict(counts=counts,origin_counts=origins,destination_counts=destinations,mesh_counts=meshes,city_counts=city,
                origin_tvd=oc.total_variation_distance(normal,origin_ref) if normal else None,
                destination_tvd=oc.total_variation_distance(dn,dest_ref),analysis_target=target.as_dict(),directory=str(directory))
