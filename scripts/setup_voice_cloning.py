"""Install the optional Coqui engine and its isolated, compatible Transformers stack."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COMPATIBLE = ('transformers==4.57.6', 'huggingface-hub==0.36.0', 'tokenizers==0.22.2')


def main():
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(ROOT / 'requirements-voice-cloning.txt')],
                   check=True, cwd=ROOT)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--target', str(ROOT / 'runtime' / 'xtts-deps'),
                    '--upgrade', '--no-deps', *COMPATIBLE], check=True, cwd=ROOT)
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'setup_shared_ffmpeg.py')], check=True, cwd=ROOT)
    subprocess.run([sys.executable, '-c', 'from app.voice.cloning import load_xtts_types; '
                    'load_xtts_types(); print("XTTS engine imports passed")'], check=True, cwd=ROOT)
    print('Engine installed. Select a locally licensed XTTS checkpoint and a consented reference WAV to synthesize.')


if __name__ == '__main__':
    main()
