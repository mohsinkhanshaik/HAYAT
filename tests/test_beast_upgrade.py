"""Quality boundaries for training, natural speech, local voice matching and diagnostics."""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from app.doctor import inspect_runtime
from app.modes import ModeManager
from app.training.lifecycle import PrivateFineTuneLifecycle
from app.training.pipeline import TrainingDataPipeline
from app.voice.cloning import LocalVoiceCloneTTS, reference_info
from app.voice.rendering import collect_piper, spoken_text
from app.voice.tts import PiperTTS
from scripts.train_hayat_adapter import _conversation, _validate_corpus


def test_beast_mode_uses_existing_deep_reasoning_and_critique(tmp_path):
    modes = ModeManager(tmp_path / 'mode.json')
    assert {mode.value for mode in modes.set('beast mode')} == {'DEEP', 'ADVERSARIAL'}
    assert modes.snapshot()['configured'] == ['DEEP', 'ADVERSARIAL']
    assert {mode.value for mode in modes.set('BEAST+LOCAL')} == {'DEEP', 'ADVERSARIAL', 'LOCAL_ONLY'}


def test_speech_formats_headings_links_and_lists_without_destroying_numbers_or_languages():
    text = spoken_text('## Your plan\n- **First**: 3.14 metres\n- [Source](https://example.org)\nمرحبا 🙂')
    assert text == 'Your plan. First: 3.14 metres. Source. مرحبا'
    assert spoken_text('Hello') == 'Hello'


def test_sentence_pauses_speed_and_wav_volume_are_consistent(tmp_path):
    calls = []
    class Voice:
        def synthesize(self, text, **options):
            calls.append((text, options))
            return [SimpleNamespace(sample_rate=16000, audio_float_array=np.full(160, 0.4)) for _ in range(2)]
    provider = PiperTTS({'voice_root': tmp_path, 'speed': 1.25, 'volume': 0.5, 'sentence_pause': 0.1},
                        SimpleNamespace(resolve=lambda *args: None))
    provider._load = lambda: Voice()
    played = []
    provider._play = lambda samples, rate: played.append(samples)
    actual = provider.synthesize_to_array('One. Two.')
    path = provider.synthesize_to_file('One. Two.', tmp_path / 'speech.wav')
    with wave.open(str(path)) as source:
        saved = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2') / 32767
    assert len(actual) == 160 * 2 + 1600
    assert actual.max() == pytest.approx(0.2)
    assert saved == pytest.approx(actual, abs=1 / 32767)
    assert calls[0][1]['syn_config'].length_scale == pytest.approx(0.8)
    assert len(played) == 1, 'Export must not also play the speech'


@pytest.mark.parametrize('rate,audio', [(0, [0.2]), (16000, [float('nan')]), (16000, [[0.1, 0.2]])])
def test_bad_synthesized_audio_is_refused(rate, audio):
    voice = SimpleNamespace(synthesize=lambda *args, **kwargs: [SimpleNamespace(sample_rate=rate, audio_float_array=audio)])
    with pytest.raises(ValueError, match='Speech output'):
        collect_piper(voice, 'Hello', 1)


def recording(path, *, silent=False):
    samples = np.zeros(64000) if silent else np.sin(np.arange(64000) * 0.04) * 0.15
    with wave.open(str(path), 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000)
        target.writeframes((samples * 32767).astype('<i2').tobytes())
    return path


def test_reference_voice_requires_an_audible_recording_and_explicit_speaker_consent(tmp_path):
    reference = recording(tmp_path / 'voice.wav')
    assert reference_info(reference)['seconds'] == 4
    with pytest.raises(ValueError, match='silent'):
        reference_info(recording(tmp_path / 'silent.wav', silent=True))
    provider = LocalVoiceCloneTTS({'reference_audio': reference, 'checkpoint_dir': tmp_path},
                                 SimpleNamespace(resolve=lambda *args: None))
    try:
        status = provider.status()
        assert not status['available'] and not status['synthesis_verified']
        assert any('agreed' in reason for reason in status['blockers'])
        with pytest.raises(RuntimeError, match='agreed'):
            provider._synthesize('Hello')
    finally:
        provider.close()


def test_configured_clone_reports_verified_only_after_audio_was_returned(tmp_path, monkeypatch):
    reference = recording(tmp_path / 'voice.wav')
    for filename in ('config.json', 'model.pth', 'vocab.json'):
        (tmp_path / filename).write_text('{}')
    monkeypatch.setattr('app.voice.cloning.importlib.util.find_spec', lambda name: object())
    monkeypatch.setattr('app.resources.heavy', lambda *args, **kwargs: nullcontext())
    provider = LocalVoiceCloneTTS({'reference_audio': reference, 'checkpoint_dir': tmp_path, 'speaker_consent': True},
                                 SimpleNamespace(resolve=lambda *args: None))
    try:
        assert provider.status()['available'] and not provider.status()['synthesis_verified']
        provider._synth._request = lambda *args: {'samples': np.ones(16, dtype='<f4').tobytes(), 'sample_rate':24000}
        assert provider._synthesize('A local voice')['sample_rate'] == 24000
        assert provider.status()['synthesis_verified']
    finally:
        provider.close()


def test_training_promotion_rechecks_changed_content_and_proof(tmp_path):
    pipeline = TrainingDataPipeline(tmp_path, config={})
    record = pipeline.ingest('conversation', {'input':'question', 'ideal_response':'answer'}, provenance={'source':'owner'})
    pipeline.validate(record['id'], human_approved=True)
    with pipeline._connect() as db:
        db.execute('UPDATE training_records SET validation_json=? WHERE id=?', ('{"verification":{"passed":"false"}}', record['id']))
    with pytest.raises(ValueError, match='invalid training record'):
        pipeline.promote(record['id'])


def test_review_filters_category_privacy_and_literal_search(tmp_path):
    pipeline = TrainingDataPipeline(tmp_path, config={'private_data':{'enabled':True, 'local_only':True}})
    first = pipeline.ingest('routing', {'input':'find 100% of examples'}, provenance={'source':'owner'})
    pipeline.ingest('conversation', {'input':'private answer'}, provenance={'source':'owner'}, privacy_scope='PRIVATE_LOCAL', owner_consent=True)
    assert pipeline.pending_review(category='routing', query='100%')['records'][0]['id'] == first['id']
    assert pipeline.pending_review(privacy_scope='PRIVATE_LOCAL')['total'] == 1
    assert pipeline.pending_review(query='does not match')['total'] == 0
    with pytest.raises(ValueError):
        pipeline.pending_review(order='DROP TABLE')


def corpus(tmp_path):
    files = {}
    for split in ('train', 'validation', 'test'):
        sample = {'input':f'Question for {split}', 'ideal_response':f'Answer for {split}'}
        row = {'state':'HELD_OUT' if split=='test' else 'TRAINING_READY', 'split':split, 'category':'conversation',
               'privacy_scope':'STANDARD', 'local_only':True, 'provenance':{'source':'fixture'},
               'validation':{'human_approved':True}, 'sample':sample,
               'content_hash':hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest()}
        path = tmp_path / f'{split}.jsonl'; path.write_text(json.dumps(row)+'\n')
        files[split] = str(path)
    return files


def test_actual_corpus_is_checked_for_integrity_and_cross_split_target_leaks(tmp_path):
    files = corpus(tmp_path)
    assert _validate_corpus(files) == {'train':1, 'validation':1, 'test':1}
    train = json.loads(Path(files['train']).read_text())
    train.update(split='test', state='HELD_OUT')
    Path(files['test']).write_text(json.dumps(train)+'\n')
    with pytest.raises(ValueError, match='duplicate training target'):
        _validate_corpus(files)
    train['sample']['ideal_response'] = 'tampered target'
    Path(files['test']).write_text(json.dumps(train)+'\n')
    with pytest.raises(ValueError, match='content changed'):
        _validate_corpus(files)


@pytest.mark.parametrize('sample', [{'messages':[]}, {'messages':[{'role':'user','content':'hello'}]}, {'input':'question', 'response':'   '}])
def test_supervised_training_requires_an_actual_answer_target(sample):
    with pytest.raises(ValueError):
        _conversation({'sample':sample})


def test_evaluation_never_promotes_nan_or_truthy_string_results(tmp_path):
    lifecycle = PrivateFineTuneLifecycle(TrainingDataPipeline(tmp_path / 'training', config={}), tmp_path, config={})
    candidate = lifecycle.prepare()
    candidate['state'] = 'TRAINED_UNVERIFIED'
    lifecycle._write(lifecycle.root / candidate['candidate_id'] / 'manifest.json', candidate)
    values = dict(baseline_score=0.7, candidate_score=0.8, baseline_latency_ms=20, candidate_latency_ms=22, golden_passed=True, safety_passed=True)
    for bad in ({'candidate_score':float('nan')}, {'candidate_latency_ms':-1}, {'golden_passed':'false'}):
        with pytest.raises(ValueError):
            lifecycle.record_evaluation(candidate['candidate_id'], **{**values, **bad})
    assert lifecycle.record_evaluation(candidate['candidate_id'], **values)['state'] == 'EVALUATED_PASS'
    with pytest.raises(ValueError, match='Unsupported training profile'):
        lifecycle.gate(profile='typo')


def test_health_marks_unavailable_components_as_degraded_instead_of_ready():
    runtime = SimpleNamespace(router=SimpleNamespace(available=lambda:{}), voice=None,
                              registry=SimpleNamespace(names=lambda:[]))
    result = inspect_runtime(runtime)
    assert result['checks']['model']['status'] == 'degraded'
    assert result['checks']['voice']['status'] == 'degraded'
    assert not result['ready'] and {'model','voice'} <= set(result['attention'])


@pytest.mark.parametrize('reply,expected', [('LOCAL_MODEL_OK',0), ('An unrelated answer',1)])
def test_standalone_model_check_uses_the_portable_endpoint_and_checks_the_reply(monkeypatch, reply, expected):
    from scripts import model_check
    import os
    seen = []
    def activate():
        monkeypatch.setenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11435')
    def get():
        seen.append(os.environ['OLLAMA_BASE_URL'])
        return SimpleNamespace(generate=lambda *args, **kwargs: SimpleNamespace(provider='ollama', model='hayat:latest', content=reply))
    monkeypatch.setattr(model_check, 'activate', activate)
    monkeypatch.setattr(model_check, 'ModelRouter', lambda: SimpleNamespace(get=get))
    assert model_check.main() == expected
    assert seen == ['http://127.0.0.1:11435']


def test_the_doctor_says_why_no_model_answered(monkeypatch):
    from types import SimpleNamespace
    from app.doctor import _model_check
    from app.portable import PORT

    runtime = SimpleNamespace(router=SimpleNamespace(available=lambda: {"ollama": [], "lm_studio": []}))
    monkeypatch.setenv("OLLAMA_HOST", f"127.0.0.1:{PORT}")
    detail = _model_check(runtime)
    assert detail["available"] is False and f"port {PORT}" in detail["why"] and "starts with HAYAT" in detail["why"]
    ready = SimpleNamespace(router=SimpleNamespace(available=lambda: {"ollama": ["qwen"], "lm_studio": []}))
    assert "why" not in _model_check(ready)
