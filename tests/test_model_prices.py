"""Audio billing units and unknown prices must never be guessed."""
import json
import pytest
from apollo_models import price_from_page, token_price, discover_catalog, discover_price

def page(prices):
    payload = json.dumps({"display_pricing": prices}, separators=(',', ':'))
    return '<script>self.__next_f.push(' + json.dumps([1, payload]) + ')</script>'

@pytest.mark.parametrize('unit,price,multiplier,expected', [
    ('/hour','0.1',1,'$0.1 / Std. Audio'),
    ('/second','0.0001',1,'$0.0001 / Sek. Audio'),
    ('/M tokens','0.0000025',1000000,'$2.5 / Mio. Tokens'),
    ('/hour','0',1,'$0 / Std. Audio')])
def test_explicit_audio_units(unit,price,multiplier,expected):
    assert expected in price_from_page(page([{'price':price,'unitLabel':unit,'displayMultiplier':multiplier,'sku_label':'Audio'}]))

@pytest.mark.parametrize('value', [None, '-1', 'NaN', 'Infinity', 'wat'])
def test_invalid_price_is_unavailable(value):
    assert price_from_page(page([{'price':value,'unitLabel':'/hour'}])) == 'Preis nicht verfügbar'

def test_missing_units_and_changed_page_do_not_claim_a_free_model():
    assert price_from_page(page([{'price':'0.1'}])) == 'Preis nicht verfügbar'
    assert price_from_page('<html>No price metadata</html>') == 'Preis nicht verfügbar'
    assert token_price({}) == 'Preis nicht verfügbar'
    assert token_price({'prompt':'NaN','completion':'0'}) == 'Preis nicht verfügbar'
    assert '$1.00 Eingabe' in token_price({'prompt':'0.000001','completion':'0.000005'})

def test_public_audio_catalog_does_not_treat_prompt_value_as_a_token_price():
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'data':[{'id':'audio/asr','pricing':{'prompt':'0.1'},
            'architecture':{'input_modalities':['audio'],'output_modalities':['transcription']}}]}
    data = discover_catalog('transcription', lambda *args, **kwargs:Response())
    assert data['audio/asr']['price'] == 'Preis wird geladen …'

def test_public_price_request_contains_no_credentials():
    calls=[]
    class Response:
        text=page([{'price':'0.1','unitLabel':'/hour'}])
        def raise_for_status(self):pass
    def get(url,**kwargs): calls.append((url,kwargs));return Response()
    assert 'Std. Audio' in discover_price('audio/model',get)
    assert calls==[('https://openrouter.ai/audio/model',{'timeout':15})]
    with pytest.raises(ValueError):discover_price('https://other.example/x',get)
    assert len(calls)==1
