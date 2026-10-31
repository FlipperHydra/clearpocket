from app.security import (csrf_token_for, hash_password, hash_token, identifier_hash, needs_rehash,
                          new_session_token, tokens_equal, verify_password)


def test_password_hash_is_argon2id_and_verifies():
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$") and "correct" not in h
    assert verify_password(h, "correct horse battery")
    assert not verify_password(h, "wrong")
    assert not needs_rehash(h)


def test_unknown_user_never_verifies():
    assert verify_password(None, "anything") is False


def test_tokens():
    a, b = new_session_token(), new_session_token()
    assert a != b and len(a) >= 40
    assert hash_token(a) != a and len(hash_token(a)) == 64
    c1, c2 = csrf_token_for(a, "k" * 32), csrf_token_for(b, "k" * 32)
    assert c1 != c2 and tokens_equal(c1, csrf_token_for(a, "k" * 32))
    assert csrf_token_for(a, "k" * 32) != csrf_token_for(a, "j" * 32)
    assert identifier_hash("a@b.c", "k" * 32) != "a@b.c"
