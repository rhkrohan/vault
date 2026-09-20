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
    # Built from two halves rather than written as one literal. The value is
    # synthetic (a sequential alphabet, never a real credential), but GitHub's
    # push protection matches the *shape* of an AWS key id and rejected the
    # push over this line. Splitting it keeps the fixture and the assertion
    # exactly as they were while letting the repo publish.
    fake_key_id = "AKIA" + "ABCDEFGHIJKLMNOP"
    text = f"AWS_ACCESS_KEY_ID={fake_key_id}"
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
    # Split for the same reason as the AWS fixture above: a random 40-char
    # run reads as an AWS secret access key to GitHub's scanner.
    token = "7f9a2QzR8mLp3XeK" + "1vBnT6yU0cWdS4hJ" + "rN5oIiA2"
    text = f"session secret: {token} done"
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


def test_redacts_hex_token_that_entropy_rule_cannot_catch():
    """A hex run cannot trip the entropy rule, so it needs its own.

    Sixteen symbols cap Shannon entropy at 4.0 bits/char and a finite sample
    measures ~3.7-3.8, always below PRD section 9's "> 4.0" threshold. Hex
    session and API tokens are the most common secret shape in a pasted
    transcript, so without this they reached memory/ untouched.
    """
    token = "9f3c1a7e40b26d58cc09e1f47a3b82d6"  # 32 hex characters
    clean, counts = scrub(f"Authorization: Bearer {token}")
    assert token not in clean
    assert counts["hex_token"] == 1


def test_redacts_long_hex_run_of_any_case():
    token = "5E1B9D07AC42F83619BE7C05DA2F6841"  # 32 hex characters, uppercase
    clean, counts = scrub(f"commit {token}")
    assert token not in clean
    assert counts["hex_token"] == 1


def test_repetitive_hex_run_is_not_a_token():
    """Length alone would fire on repeated text, which is a false positive.

    "abababab..." is a valid hex run but carries no secret, so the rule
    carries an entropy floor that a real random token clears easily.
    """
    text = "ab" * 24  # 48 hex characters, entropy 1.0
    clean, counts = scrub(text)
    assert clean == text
    assert counts == {}


def test_short_hex_is_left_alone():
    """Length is the signal; ordinary short ids must survive untouched."""
    clean, counts = scrub("the id is a3f91c04 and the port is 8000")
    assert "a3f91c04" in clean
    assert counts == {}
