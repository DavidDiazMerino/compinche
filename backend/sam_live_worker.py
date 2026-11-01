"""Private stdin worker inside the isolated SAM container; weights load once."""
import json
from pathlib import Path
import sys
import time
import traceback
import sam_worker

PREFIX='COMPINCHE_SAM_EVENT '
def send(value):print(PREFIX+json.dumps(value),flush=True)

def main():
    start=time.monotonic()
    predictor=sam_worker.build_sam3_predictor(version='sam3.1',compile=False,warm_up=False,
        max_num_objects=16,multiplex_count=16,use_fa3=False,async_loading_frames=False)
    original_init=predictor.model.init_state
    sam_worker.build_sam3_predictor=lambda **kwargs:predictor
    load=time.monotonic()-start
    send({'ready':True,'load_seconds':load})
    for line in sys.stdin:
        item=None
        try:
            item=json.loads(line);source=Path(item['input']).resolve();out=Path(item['output']).resolve()
            if not source.is_relative_to('/media') or not out.is_relative_to('/output'):raise ValueError('Outside job roots')
            predictor.model.init_state=original_init
            sys.argv=['sam_worker','--input',str(source),'--output',str(out),'--target',item['target']]
            sam_worker.main()
            send({'id':item['id'],'ok':True,'resident_load_seconds':load})
        except Exception:
            traceback.print_exc(file=sys.stderr)
            send({'id':item.get('id') if isinstance(item,dict) else None,'ok':False})
if __name__=='__main__':main()
