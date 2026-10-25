"""Real local OSS parsers and hashing inside actual bounded subprocesses."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import threading

from PIL import Image, ImageDraw, ImageFilter, PngImagePlugin, TiffImagePlugin
import pytest

from app.perception import image_tools


def sample(path, *, size=(96, 64), color='white'):
    image = Image.new('RGB', size, color)
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((3, 4, size[0] // 2, size[1] // 2), fill='black')
    drawing.line((0, size[1] - 1, size[0] - 1, 0), fill='red', width=3)
    image.save(path)
    return path


def test_status_reports_actual_pinned_packages_modules_and_licenses():
    status = image_tools.toolkit_status()
    assert status['local_image_toolkit'] and status['available']
    packages = {row['module']: row for row in status['packages']}
    assert packages['imagehash']['version'] == '4.3.2'
    assert packages['exifread']['version'] == '3.5.1'
    for row in status['packages']:
        assert row['available'] and Path(row['module_path']).is_file() and row['license']
    assert status['limits']['concurrent_workers'] == 2
    assert 'not a general code sandbox' in status['limits']['parser_isolation']


def test_inspect_runs_real_worker_and_returns_actual_file_evidence_without_writes(tmp_path):
    path = sample(tmp_path / 'source.png')
    before = {row.name: row.read_bytes() for row in tmp_path.iterdir()}
    value = image_tools.inspect_image(path)
    assert value['operation'] == 'inspect' and value['local_image_inspection']
    assert value['local_only'] and value['identity_inference'] is False
    assert value['width'] == 96 and value['height'] == 64 and value['format'] == 'PNG'
    assert value['file_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert value['worker_limits']['max_pixels'] == 24_000_000
    quality = value['quality']
    assert 0 <= quality['mean_luminance'] <= 255
    assert 0 <= quality['clipped_dark_fraction'] <= 1 and 0 <= quality['clipped_light_fraction'] <= 1
    assert math.isfinite(quality['laplacian_variance']) and 'heuristics' in quality['basis']
    assert before == {row.name: row.read_bytes() for row in tmp_path.iterdir()}
    assert not [thread for thread in threading.enumerate() if thread.name == 'hayat-image-worker-output']


def test_only_technical_exif_is_returned_and_orientation_is_applied(tmp_path):
    path = tmp_path / 'metadata.jpg'
    image = Image.new('RGB', (30, 18), 'blue')
    exif = Image.Exif()
    for key, value in {271: 'SyntheticCamera', 272: 'SyntheticModel', 274: 6, 305: 'SyntheticEditor',
                       315: 'PrivateOwnerCanary', 42033: 'PrivateSerialCanary',
                       270: 'PrivateCommentsCanary'}.items():
        exif[key] = value
    rational = TiffImagePlugin.IFDRational
    exif[34853] = {1: 'N', 2: (rational(40), rational(1), rational(2)),
                   3: 'W', 4: (rational(73), rational(1), rational(2))}
    image.save(path, exif=exif)
    value = image_tools.inspect_image(path)
    assert value['orientation'] == 6
    assert (value['display_width'], value['display_height']) == (18, 30)
    assert set(value['technical_metadata']) <= {'Image Make', 'Image Model', 'Image Orientation', 'Image Software'}
    assert value['technical_metadata']['Image Make'] == 'SyntheticCamera'
    serialized = json.dumps(value)
    for secret in ['PrivateOwnerCanary', 'PrivateSerialCanary', 'PrivateCommentsCanary', 'GPSLatitude', 'GPSLongitude']:
        assert secret not in serialized


def test_equality_distinguishes_original_bytes_decoded_pixels_and_metadata(tmp_path):
    first = sample(tmp_path / 'first.png')
    second = tmp_path / 'second.png'
    with Image.open(first) as image:
        information = PngImagePlugin.PngInfo()
        information.add_text('Comment', 'A different byte stream with the same decoded pixels.')
        image.save(second, pnginfo=information, compress_level=1)
    value = image_tools.compare_images(first, second)
    assert value['local_image_comparison'] and value['operation'] == 'compare'
    assert value['byte_equal'] is False and value['pixel_equal'] is True
    assert value['phash_distance'] == 0 and value['dhash_distance'] == 0
    assert value['hash_bits'] == 64 and value['identity_inference'] is False
    identical = image_tools.compare_images(first, first)
    assert identical['byte_equal'] and identical['pixel_equal']


def test_decoded_equality_checks_rgb_even_when_alpha_is_identical(tmp_path):
    first, second = tmp_path / 'red.png', tmp_path / 'blue.png'
    Image.new('RGBA', (16, 16), (255, 0, 0, 255)).save(first)
    Image.new('RGBA', (16, 16), (0, 0, 255, 255)).save(second)
    value = image_tools.compare_images(first, second)
    assert value['pixel_equal'] is False and value['byte_equal'] is False
    # Uniform images may share perceptual hashes; equality is kept separate.
    assert 0 <= value['phash_distance'] <= 64 and 0 <= value['dhash_distance'] <= 64


def test_orientation_normalization_is_used_for_pixel_comparison(tmp_path):
    original = Image.new('RGB', (12, 8), 'black')
    ImageDraw.Draw(original).rectangle((0, 0, 3, 7), fill='yellow')
    exif = Image.Exif()
    exif[274] = 6
    first, second = tmp_path / 'oriented.png', tmp_path / 'normalized.png'
    original.save(first, exif=exif)
    original.transpose(Image.Transpose.ROTATE_270).save(second)
    value = image_tools.compare_images(first, second)
    assert value['pixel_equal'] is True and value['byte_equal'] is False
    assert value['phash_distance'] == 0 and value['dhash_distance'] == 0


def test_different_structure_changes_whole_image_hashes(tmp_path):
    first = sample(tmp_path / 'pattern.png')
    second = tmp_path / 'other.png'
    Image.new('RGB', (96, 64), 'gray').save(second)
    value = image_tools.compare_images(first, second)
    assert value['pixel_equal'] is False
    assert value['phash_distance'] > 0 or value['dhash_distance'] > 0
    assert any('not facial identity' in item for item in value['limitations'])


def test_quality_metrics_are_measured_and_not_an_identity_eligibility_label(tmp_path):
    crisp = sample(tmp_path / 'crisp.png', size=(128, 128))
    blurred = tmp_path / 'blurred.png'
    with Image.open(crisp) as image:
        image.filter(ImageFilter.GaussianBlur(7)).save(blurred)
    sharp_value, soft_value = image_tools.inspect_image(crisp), image_tools.inspect_image(blurred)
    assert sharp_value['quality']['laplacian_variance'] > soft_value['quality']['laplacian_variance']
    assert all(key not in soft_value for key in ['identity_eligible', 'identity_probability', 'person_id'])


@pytest.mark.parametrize('path', ['https://example.org/image.jpg', 'file:///C:/image.jpg', 'data:image/png;base64,AAAA',
                                 r'\\server\private\photo.jpg', '//server/private/photo.jpg', 'smb://server/photo.jpg'])
def test_network_urls_and_shares_are_rejected_before_worker_start(path):
    with pytest.raises(ValueError, match='local|network|URL'):
        image_tools.inspect_image(path)


def test_file_and_pixel_limits_are_enforced_by_parent_and_real_decoder(tmp_path):
    oversized = tmp_path / 'oversized.bin'
    with oversized.open('wb') as stream:
        stream.truncate(image_tools.MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError, match='25 MiB'):
        image_tools.inspect_image(oversized)
    large = tmp_path / 'large.png'
    Image.new('L', (6001, 4000)).save(large)
    with pytest.raises(ValueError, match='rejected'):
        image_tools.inspect_image(large)


def test_malformed_metadata_or_file_cannot_escape_the_worker(tmp_path):
    malformed = tmp_path / 'malformed.jpg'
    malformed.write_bytes(b'\xff\xd8\xff\xe1\x00\x20Exif\x00\x00II\x2a\x00' + b'\xff' * 80)
    with pytest.raises(ValueError, match='rejected'):
        image_tools.inspect_image(malformed)
    assert image_tools.inspect_image(sample(tmp_path / 'valid.png'))['local_image_inspection']


def test_real_worker_timeout_terminates_and_releases_its_slot(tmp_path, monkeypatch):
    path = sample(tmp_path / 'timeout.png')
    monkeypatch.setattr(image_tools, 'WORKER_TIMEOUT', .001)
    with pytest.raises(TimeoutError, match='time budget'):
        image_tools.inspect_image(path)
    monkeypatch.setattr(image_tools, 'WORKER_TIMEOUT', 20.0)
    assert image_tools.inspect_image(path)['local_only']


def test_cancellation_before_read_and_during_real_process_wait_returns_no_stale_result(tmp_path):
    path = sample(tmp_path / 'cancelled.png')
    with pytest.raises(PermissionError, match='cancelled'):
        image_tools.inspect_image(path, check=lambda: False)
    calls = []
    def check():
        calls.append(True)
        if len(calls) >= 5:
            raise PermissionError('Synthetic emergency stop')
    with pytest.raises(PermissionError, match='Synthetic emergency stop'):
        image_tools.inspect_image(path, check=check)
    assert len(calls) >= 5
    assert not [thread for thread in threading.enumerate() if thread.name == 'hayat-image-worker-output']


def test_actual_worker_output_and_input_are_bounded(tmp_path, monkeypatch):
    path = sample(tmp_path / 'bounded.png')
    monkeypatch.setattr(image_tools, 'MAX_INPUT_BYTES', 16)
    with pytest.raises(ValueError, match='request size'):
        image_tools.inspect_image(path)
    monkeypatch.setattr(image_tools, 'MAX_INPUT_BYTES', 8192)
    monkeypatch.setattr(image_tools, 'MAX_OUTPUT_BYTES', 32)
    with pytest.raises(RuntimeError, match='output boundary'):
        image_tools.inspect_image(path)


def test_formatter_delivers_evidence_metrics_and_scope_from_actual_results(tmp_path):
    path = sample(tmp_path / 'report.png')
    inspect = image_tools.format_image_tool_report(image_tools.inspect_image(path))
    compare = image_tools.format_image_tool_report(image_tools.compare_images(path, path))
    status = image_tools.format_image_tool_report(image_tools.toolkit_status())
    assert 'SHA-256:' in inspect and 'heuristic' in inspect
    assert 'Identical file bytes: True' in compare and 'not facial identity' in compare
    assert 'ExifRead' in status and 'ImageHash' in status
