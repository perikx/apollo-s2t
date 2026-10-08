"""Curated ranking, structured prices and local model stats."""
import json
import threading
import pytest
import apollo_i18n
from apollo_models import (BADGES, STT, TEXT, audio_price, curated, discover_catalog, load_stats,
                           model_note, price_text, record_attempt, token_price)


def fake_get(models, calls=None):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'data': models}
    def get(url, **kwargs):
        if calls is not None: calls.append((url, kwargs))
        return Response()
    return get


def test_ranking_is_ordered_by_tier_and_text_list_is_current():
    tiers = [row[0] for row in STT.values()]
    assert tiers == sorted(tiers, key=list(BADGES).index)
    assert next(iter(STT)) == 'microsoft/mai-transcribe-2'
    assert 'deepseek/deepseek-v4-flash-20260731' not in TEXT
    assert curated('text')['qwen/qwen3.8-flash'] == ('', 'Current inexpensive text editing')
    assert curated('transcription')['google/chirp-3'][0] == 'avoid'


def test_catalog_is_one_request_and_prices_are_numbers():
    calls = []
    audio = {'input_modalities': ['audio'], 'output_modalities': ['transcription']}
    models = [{'id': 'openai/whisper-1', 'architecture': audio}, {'id': 'brand/new', 'architecture': audio}]
    data = discover_catalog('transcription', fake_get(models, calls))
    assert data['openai/whisper-1']['price'] == ('audio', 6.0) and data['brand/new']['price'] is None
    assert calls == [('https://openrouter.ai/api/v1/models', {'params': {'output_modalities': 'transcription'}, 'timeout': 15})]


def test_text_catalog_keeps_unranked_models_and_reads_token_prices():
    text = {'input_modalities': ['text'], 'output_modalities': ['text']}
    models = [{'id': 'brand/new-model', 'architecture': text, 'pricing': {'prompt': '0.000001', 'completion': '0.000005'}},
              {'id': 'brand/no-price', 'architecture': text}]
    data = discover_catalog('text', fake_get(models))
    assert data['brand/new-model']['price'] == ('tokens', pytest.approx(1.0), pytest.approx(5.0))
    assert data['brand/no-price']['price'] is None


@pytest.mark.parametrize('pricing', [{}, {'prompt': 'NaN', 'completion': '0'}, {'prompt': '-1', 'completion': '0'}, {'prompt': 'wat', 'completion': '1'}])
def test_invalid_token_price_is_unknown(pricing):
    assert token_price(pricing) is None


def test_price_text_is_translated():
    apollo_i18n.set_language('en')
    try:
        assert price_text(audio_price('microsoft/mai-transcribe-2')) == '$1.67 / 1000 min'
        assert price_text(('tokens', 0.1, 0.4)) == '$0.10 in · $0.40 out / 1M tokens'
        assert price_text(None) == ''
        apollo_i18n.set_language('de')
        assert 'Eingabe' in price_text(('tokens', 0.1, 0.4))
    finally:
        apollo_i18n.set_language('en')


def test_note_joins_badge_stats_and_reason(tmp_path):
    apollo_i18n.set_language('en')
    ranked = curated('transcription')
    assert model_note('microsoft/mai-transcribe-2', ranked, None) == 'Best · Most accurate and fastest; can be rate limited'
    assert model_note('brand/new', ranked, None) == ''
    stat = {'ok': 47, 'fail': 3, 'median_s': 1.84}
    assert model_note('brand/new', ranked, stat) == 'You: 94% ok · 1.8 s'
    assert model_note('brand/new', ranked, {'ok': 2, 'fail': 2, 'median_s': 1.0}) == ''


def test_stats_count_attempts_and_keep_last_50_latencies(tmp_path):
    path = tmp_path / 'stats.json'
    assert load_stats(path) == {}
    for i in range(60): record_attempt(path, 'a/b', True, i)
    record_attempt(path, 'a/b', False, 99)
    stat = load_stats(path)['a/b']
    assert (stat['ok'], stat['fail']) == (60, 1) and stat['median_s'] == pytest.approx(34.5)
    assert len(json.loads(path.read_text())['a/b']['seconds']) == 50
    assert list(tmp_path.iterdir()) == [path]  # no temporary file left behind


def test_stats_never_raise_or_store_text(tmp_path):
    path = tmp_path / 'stats.json'
    path.write_text('not json')
    record_attempt(path, 'a/b', True, 1.0)
    assert load_stats(path)['a/b']['ok'] == 1
    record_attempt(tmp_path / 'missing' / 'stats.json', 'a/b', True, 1.0)  # unwritable: ignored
    assert set(json.loads(path.read_text())['a/b']) == {'ok', 'seconds'}


def test_stats_survive_concurrent_attempts(tmp_path):
    path = tmp_path / 'stats.json'
    threads = [threading.Thread(target=record_attempt, args=(path, 'a/b', True, 1.0)) for _ in range(20)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert load_stats(path)['a/b']['ok'] == 20
