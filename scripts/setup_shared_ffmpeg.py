"""Install the verified shared FFmpeg libraries needed by TorchCodec on Windows."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    import httpx
    from scripts.setup_native_analysis import provision
    target = ROOT / 'runtime' / 'ffmpeg-shared'
    if (target / 'bin' / 'avcodec-62.dll').is_file():
        print('Shared FFmpeg is installed.')
        return
    response = httpx.get('https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest', timeout=30)
    response.raise_for_status()
    asset = next(row for row in response.json()['assets'] if row['name'] == 'ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip')
    algorithm, _, digest = str(asset.get('digest', '')).partition(':')
    if algorithm != 'sha256' or len(digest) != 64:
        raise ValueError('The FFmpeg publisher did not provide a SHA-256 digest.')
    provision(asset['browser_download_url'], digest, asset['name'], target, 'bin/avcodec-62.dll', 512 * 1024**2)


if __name__ == '__main__':
    main()
