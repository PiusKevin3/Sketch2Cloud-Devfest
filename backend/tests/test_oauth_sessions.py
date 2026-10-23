from datetime import datetime, timezone

import oauth_sessions as o


def setup_module(_):
    o.init_db()


def test_ensure_aware():
    naive = datetime(2030, 1, 1, 12, 0, 0)
    assert o._ensure_aware(naive).tzinfo is timezone.utc
    aware = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert o._ensure_aware(aware) is aware
    assert o._ensure_aware(None) is None


def test_enc_dec_roundtrip_or_passthrough():
    enc = o._enc("tok-abc")
    dec = o._dec(enc)
    if o._FERNET is None:
        assert enc == "tok-abc"          # encryption disabled: identity
    else:
        assert enc.startswith("enc:") and enc != "tok-abc"
    assert dec == "tok-abc"
    # legacy plaintext rows must survive a decrypt attempt
    assert o._dec("legacy-plaintext") == "legacy-plaintext"


def test_project_persisted_on_session():
    sid = o.get_or_create_session(None)
    assert o.get_session(sid)["gcp_project"] is None
    o.set_project(sid, "my-proj")
    assert o.get_session(sid)["gcp_project"] == "my-proj"
    o.clear_tokens(sid)
    assert o.get_session(sid)["gcp_project"] is None


def test_save_and_read_tokens():
    sid = o.get_or_create_session(None)
    o.save_tokens(sid, "access-1", "refresh-1", 3600, "me@example.com")
    session = o.get_session(sid)
    assert session["access_token"] == "access-1"
    assert session["refresh_token"] == "refresh-1"
    assert session["email"] == "me@example.com"
