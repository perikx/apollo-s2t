"""Runtime rebinding preserves working keys and local form input on failure."""
from copy import deepcopy
import threading
import time
import types
import pytest
import apollo
from apollo_config import ConfigError

@pytest.fixture
def hooks(monkeypatch):
    callbacks=[]; removed=[]
    codes={'f8':8,'f9':9,'f10':10,'n':11,'m':12,'p':13,'alias':8}
    def hook(callback, suppress):
        assert suppress
        callbacks.append(callback)
        return lambda: removed.append(callback)
    monkeypatch.setattr(apollo.keyboard,'hook',hook,raising=False)
    monkeypatch.setattr(apollo.keyboard,'key_to_scan_codes',lambda key:(codes[key],),raising=False)
    calls=[]
    monkeypatch.setattr(apollo,'make_key_handler',lambda app,mode,toggle:lambda event:calls.append((mode,event.event_type)))
    return callbacks,removed,calls

def event(code,kind='down'):return types.SimpleNamespace(scan_code=code,event_type=kind)

def drain(app):app._key_events.join()  # the dispatcher thread runs the key events

def test_rebind_deactivates_old_hook_and_preserves_typeable_keys(app,hooks,monkeypatch):
    callbacks,removed,calls=hooks
    app.bind_hotkeys()
    old=callbacks[0]
    assert not old(event(8));drain(app);assert calls==[('dictate','down')]
    app.update_preferences({'hotkeys':{'dictate':'n','polish':'m','prompt':'p'}})
    assert removed==[old] and old(event(8)) is True
    new=callbacks[1]
    assert new(event(8)) is True and not new(event(11));drain(app)
    app.ui_windows.add('window-a')
    assert new(event(11)) is True;drain(app)
    assert len(calls)==2
    app.ui_windows.clear()
    assert not new(event(12));drain(app);assert calls[-1][0]=='polish'

@pytest.mark.parametrize('key',['unknown','alias'])
def test_unknown_or_alias_key_does_not_remove_working_hook(app,hooks,key):
    callbacks,removed,calls=hooks;app.bind_hotkeys();before=deepcopy(app.cfg)
    # alias maps to F8 while polish also uses F8, despite distinct spellings.
    changes={'dictate':key,'polish':'f8'}
    with pytest.raises(ConfigError):app.update_preferences({'hotkeys':changes})
    assert app.cfg==before and not removed and len(callbacks)==1
    assert not callbacks[0](event(8))

def test_rebind_disk_failure_keeps_old_dispatch(app,hooks,monkeypatch):
    callbacks,removed,calls=hooks;app.bind_hotkeys();before=deepcopy(app.cfg)
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr(apollo,'save_config',fail)
    with pytest.raises(OSError):app.update_preferences({'hotkeys':{'dictate':'n'}})
    assert app.cfg==before and removed==[callbacks[1]]
    assert callbacks[1](event(11)) is True and not callbacks[0](event(8))

def test_recording_blocks_rebinding(app,hooks):
    callbacks,removed,calls=hooks;app.bind_hotkeys();app.recording=True
    with pytest.raises(ConfigError):app.update_preferences({'hotkeys':{'dictate':'n'}})
    app.recording=False
    assert len(callbacks)==1 and not removed

def test_held_recording_release_survives_popup_focus(app,hooks):
    callbacks,removed,calls=hooks
    app.cfg['hotkey_mode']='hold';app.bind_hotkeys()
    app.recording=True;app.active_mode='dictate';app.ui_windows.add('window-a')
    assert callbacks[0](event(8,'up')) is False  # a running recording always gets its key, even over an Apollo window
    drain(app);assert calls==[('dictate','up')]
    app.recording=False

def test_hook_does_not_wait_for_app_lock(app,hooks):
    callbacks,removed,calls=hooks;app.bind_hotkeys()
    held,done=threading.Event(),threading.Event()
    def hold():
        with app._lock:
            held.set();done.wait(3)
    threading.Thread(target=hold,daemon=True).start();held.wait(3)
    start=time.monotonic()
    try:assert not callbacks[0](event(8))
    finally:done.set()
    assert time.monotonic()-start<0.05

def test_held_key_repeat_is_ignored_until_keyup(app,hooks):
    callbacks,removed,calls=hooks
    app.cfg['hotkey_mode']='toggle';app.bind_hotkeys()
    for _ in range(5):assert not callbacks[0](event(8))
    drain(app);assert calls==[('dictate','down')]
    callbacks[0](event(8,'up'));callbacks[0](event(8));drain(app)
    assert [kind for _,kind in calls]==['down','up','down']
