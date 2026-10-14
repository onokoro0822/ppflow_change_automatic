"""Explicit analysis target; IDs distinguish co-located retail facilities."""
from dataclasses import dataclass, asdict
import csv, json, math
from pathlib import Path
from itertools import zip_longest

@dataclass(frozen=True)
class AnalysisTarget:
    meshCode: str = '52366700'
    gcode: str = '23105'
    longitude: float = 136.88397984
    latitude: float = 35.16967402
    facilityId: int | None = None
    tolerance: float = 1e-6
    source: str = 'legacy coordinate target'

    @classmethod
    def load(cls, path):
        from pflow_capacity_analysis import third_mesh_code
        obj=cls(**json.loads(Path(path).read_text()))
        if not all(math.isfinite(v) for v in (obj.longitude,obj.latitude,obj.tolerance)) or obj.tolerance<0:
            raise ValueError('Invalid target coordinate/tolerance')
        if third_mesh_code(obj.longitude,obj.latitude)!=obj.meshCode:
            raise ValueError('Target coordinate and mesh disagree')
        if obj.facilityId is not None and obj.facilityId<=0: raise ValueError('Invalid facility ID')
        return obj

    def matches(self,row,trace=None):
        if self.facilityId is not None:
            if trace is None or 'facility_id' not in trace: raise ValueError('Facility-ID trace required; coordinate fallback forbidden')
            return trace['facility_id']==str(self.facilityId)
        return abs(float(row['lon'])-self.longitude)<=self.tolerance and abs(float(row['lat'])-self.latitude)<=self.tolerance

    def rows(self,path):
        from pflow_capacity_analysis import _iter_rows
        if self.facilityId is None:
            for row in _iter_rows(path): yield row,None
            return
        with Path(str(path)+'.branches.tsv').open() as handle:
            reader=csv.DictReader(handle,delimiter='\t'); last=None; index=0
            for row,trace in zip_longest(_iter_rows(path),reader):
                if row is None or trace is None: raise ValueError('Trace length mismatch')
                index=index+1 if last==row['person_id'] else 0;last=row['person_id']
                if any(str(row[k])!=trace[t] for k,t in [('person_id','person_id'),('start','start'),('purpose','purpose')]) or int(trace['activity_index'])!=index:
                    raise ValueError('Trace row mismatch')
                yield row,trace

    def as_dict(self): return asdict(self)
