"""One local CPU voice model kept warm; NDJSON is private parent/child IPC."""
import argparse
import json
from pathlib import Path
import sys
import wave

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--model',required=True);args=parser.parse_args()
    import onnxruntime as ort
    from piper import PiperVoice, PiperConfig, SynthesisConfig
    model=Path(args.model)
    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
    voice=PiperVoice(session=ort.InferenceSession(str(model),sess_options=options,providers=['CPUExecutionProvider']),
        config=PiperConfig.from_dict(json.loads(Path(str(model)+'.json').read_text())))
    print(json.dumps({'ready':True}),flush=True)
    for line in sys.stdin:
        try:
            item=json.loads(line)
            with wave.open(item['path'],'wb') as output:
                voice.synthesize_wav(item['text'],output,syn_config=SynthesisConfig(length_scale=1.0,volume=.9))
            print(json.dumps({'id':item['id'],'ok':True}),flush=True)
        except Exception:
            print(json.dumps({'ok':False,'error':'local_voice_failed'}),flush=True)
if __name__=='__main__':main()
