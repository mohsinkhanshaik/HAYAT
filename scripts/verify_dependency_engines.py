"""Exercise installed local engines using generated data; no sensors or accounts."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    checks = {}
    def probe(name, command, timeout=120):
        print(f'Checking {name}...', flush=True)
        try:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=timeout)
            checks[name] = {'passed': result.returncode == 0, 'output': result.stdout[-4000:], 'error': result.stderr[-4000:]}
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks[name] = {'passed': False, 'error': str(exc)}
    def python(name, code, executable=sys.executable, timeout=120):
        probe(name, [str(executable), '-c', code], timeout)

    python('onnx_cuda_computation', '''import json,tempfile
import numpy as np, onnx, onnxruntime as ort
from onnx import helper, TensorProto
from pathlib import Path
ort.preload_dlls()
with tempfile.TemporaryDirectory(prefix='hayat-onnx-') as folder:
    graph=helper.make_graph([helper.make_node('MatMul',['a','b'],['c'])],'check',
        [helper.make_tensor_value_info(n,TensorProto.FLOAT,[64,64]) for n in ['a','b']],
        [helper.make_tensor_value_info('c',TensorProto.FLOAT,[64,64])])
    model=helper.make_model(graph,opset_imports=[helper.make_opsetid('',17)],ir_version=10)
    options=ort.SessionOptions();options.enable_profiling=True;options.profile_file_prefix=str(Path(folder)/'profile')
    session=ort.InferenceSession(model.SerializeToString(),sess_options=options,
        providers=[('CUDAExecutionProvider',{'gpu_mem_limit':268435456}),'CPUExecutionProvider'])
    result=session.run(None,{'a':np.ones((64,64),np.float32),'b':np.ones((64,64),np.float32)})[0]
    profile=json.loads(Path(session.end_profiling()).read_text())
    assert np.allclose(result,64)
    assert any(row.get('args',{}).get('provider')=='CUDAExecutionProvider' for row in profile), 'CUDA did not execute the graph'
    print('Verified real CUDA MatMul, numeric output, and execution profile')
    del session
''')
    python('browser_local_page', '''from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page();page.set_content('<title>HAYAT dependency check</title><button>Ready</button>')
    assert page.title()=='HAYAT dependency check' and page.get_by_role('button',name='Ready').count()==1
    browser.close();print('Chromium launched and read a local page')
''')
    python('video_engines', '''import subprocess,tempfile,json
from pathlib import Path
from app.video_player_worker import load_vlc
vlc=load_vlc();print('libVLC:',vlc.libvlc_get_version().decode())
with tempfile.TemporaryDirectory(prefix='hayat-video-') as folder:
    target=Path(folder)/'fixture.mp4'
    subprocess.run(['runtime/ffmpeg/bin/ffmpeg.exe','-v','error','-f','lavfi','-i','color=c=blue:s=64x64:r=10',
                    '-t','0.2','-threads','1','-y',str(target)],check=True,timeout=20)
    result=subprocess.run(['runtime/ffmpeg/bin/ffprobe.exe','-v','quiet','-show_streams','-of','json',str(target)],
                           capture_output=True,text=True,check=True,timeout=10)
    assert json.loads(result.stdout)['streams'][0]['width']==64
    print('FFmpeg encoded and FFprobe read a generated video')
''')
    python('ocr_engines', '''from pathlib import Path
import tempfile
from PIL import Image,ImageDraw,ImageFont
import pytesseract
from app.perception.camera.ocr import WindowsMediaOcr
with tempfile.TemporaryDirectory(prefix='hayat-ocr-') as folder:
    image=Image.new('RGB',(640,160),'white')
    ImageDraw.Draw(image).text((20,35),'HAYAT 12345',fill='black',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',48))
    path=Path(folder)/'fixture.png';image.save(path)
    pytesseract.pytesseract.tesseract_cmd='runtime/tesseract/tesseract.exe'
    assert '12345' in pytesseract.image_to_string(image)
    result=WindowsMediaOcr().read(path)
    assert result['available'] and '12345' in result['text']
    print('Tesseract and Windows OCR read generated text')
''')
    python('piper_synthesis', '''import numpy as np
from app.config import load_settings
from app.voice.tts import PiperTTS
from app.voice.rendering import collect_piper
provider=PiperTTS(load_settings()['voice']['tts'])
assert provider.installed
samples,rate=collect_piper(provider._load(),'HAYAT dependency verification.',1.0,0.16)
assert len(samples)>rate/2 and np.isfinite(samples).all()
print('Piper generated',len(samples),'samples at',rate,'Hz without audio playback')
''')
    python('whisper_transcription', '''import numpy as np
from faster_whisper import WhisperModel
model=WhisperModel('base.en',device='cpu',compute_type='int8',download_root='data/models/whisper',local_files_only=True)
segments,_=model.transcribe(np.zeros(16000,dtype=np.float32),language='en',beam_size=1,vad_filter=False)
list(segments);print('Whisper loaded the local model and processed generated silence')
''')
    python('wakeword_model', '''import numpy as np
from app.voice.wake_engine import OpenWakeWordGate
gate=OpenWakeWordGate();assert gate.ready()[0]
assert not gate.detector()(np.zeros(1280,dtype=np.int16))
print('Wake model loaded and scored generated silence')
''')
    python('xtts_engine', '''from app.voice.cloning import load_xtts_types
config,model=load_xtts_types();assert config and model
import tempfile,wave
from pathlib import Path
from torchcodec.decoders import AudioDecoder
with tempfile.TemporaryDirectory(prefix='hayat-xtts-') as folder:
    path=Path(folder)/'silence.wav'
    with wave.open(str(path),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000);output.writeframes(b'\\0\\0'*24000)
    decoded=AudioDecoder(str(path)).get_all_samples()
    assert decoded.data.shape[-1]==24000
print('XTTS classes and native WAV decoder passed; licensed checkpoint/reference still required')
''')
    python('training_cuda', '''import torch,transformers,peft,trl,datasets,accelerate,bitsandbytes
assert torch.cuda.is_available()
a=torch.ones((64,64),device='cuda');r=(a@a).cpu();assert torch.all(r==64)
print('CUDA computation and training-library imports passed:',torch.__version__,transformers.__version__)
''', ROOT / '.venv-cuda' / 'Scripts' / 'python.exe')
    python('wabt_analysis', '''import json
from pathlib import Path
from app.reverse_engineer import ReverseEngineer
folder=Path('.test-runs/wasm-analysis');folder.mkdir(parents=True,exist_ok=True)
target=folder/'fixture.wasm';target.write_bytes(bytes.fromhex('0061736d01000000'))
engine=ReverseEngineer()
try:
    result=engine.read('inspect_wasm_artifact',{'path':str(target.resolve()),'detail':'summary'})
    assert result['ok'];print('WABT validated a generated empty WASM module through REA')
finally:engine.close()
''')
    report = ROOT / '.test-runs' / 'dependency-engine-verification.json'
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps({'passed': all(row['passed'] for row in checks.values()), 'checks': checks}, indent=2), encoding='utf-8')
    print(json.dumps({name: row['passed'] for name, row in checks.items()}, indent=2))
    return 0 if all(row['passed'] for row in checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
