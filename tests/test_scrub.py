from vault.scrub import scrub


def test_redacts_anthropic_api_key():
    text = "here is my key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789 ok"
    clean, counts = scrub(text)
    assert "sk-ant" not in clean
    assert "[REDACTED:api_key]" in clean
    assert counts["api_key"] == 1


def test_redacts_generic_sk_key():
    text = "key: sk-abcdefghijklmnopqrstuvwxyz123456"
    clean, counts = scrub(text)
    assert "[REDACTED:api_key]" in clean
    assert counts["api_key"] == 1


def test_redacts_aws_key():
    text = "AWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP"
    clean, counts = scrub(text)
    assert "AKIA" not in clean
    assert counts["aws_key"] == 1


def test_redacts_github_token():
    text = "token ghp_abcdefghijklmnopqrstuvwxyz0123456789 in the readme"
    clean, counts = scrub(text)
    assert "ghp_" not in clean
    assert counts["github_token"] == 1


def test_redacts_runpod_key():
    text = "RUNPOD_API_KEY=rpa_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    clean, counts = scrub(text)
    assert "rpa_" not in clean
    assert counts["runpod_key"] == 1


def test_redacts_jwt():
    text = (
        "auth header: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c end"
    )
    clean, counts = scrub(text)
    assert "eyJ" not in clean
    assert counts["jwt"] == 1


def test_redacts_luhn_valid_card_number():
    text = "card 4111111111111111 on file"
    clean, counts = scrub(text)
    assert "4111111111111111" not in clean
    assert counts["credit_card"] == 1


def test_does_not_redact_luhn_invalid_digit_run():
    text = "order number 1234567890123 was placed"
    clean, counts = scrub(text)
    assert "1234567890123" in clean
    assert "credit_card" not in counts


def test_redacts_ssn():
    text = "SSN on file: 219-09-9999 for verification"
    clean, counts = scrub(text)
    assert "219-09-9999" not in clean
    assert counts["ssn"] == 1


def test_redacts_high_entropy_token():
    text = "session secret: 7f9a2QzR8mLp3XeK1vBnT6yU0cWdS4hJrN5oIiA2 done"
    clean, counts = scrub(text)
    assert counts.get("high_entropy_token") == 1
    assert "[REDACTED:high_entropy_token]" in clean


def test_true_negative_normal_sentence_untouched():
    text = (
        "We decided to go with Clerk for authentication because we need SSO "
        "support for contractors from a partner org."
    )
    clean, counts = scrub(text)
    assert clean == text
    assert counts == {}


def test_true_negative_long_english_word_run_low_entropy():
    text = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # 40 chars, entropy 0
    clean, counts = scrub(text)
    assert clean == text
    assert counts == {}
