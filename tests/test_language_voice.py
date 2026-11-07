import pytest

from guardian.language import detect_language, parse_command
from guardian.voice.evaluate import SampleResult, cer, summarize, voice_status, wer

PROJECTS = {"site-one": "Docs Media", "app-one": "Billing App"}


@pytest.mark.parametrize("text,lang", [
    ("ഇന്നത്തെ റിപ്പോർട്ട് തരൂ", "ml"),
    ("innathe report tharu", "manglish"),
    ("site-one check cheyyu", "manglish"),
    ("show me the report", "en"),
    ("hi nigal aran", "manglish"),            # real message from the first laptop test
    ("ningal enthokke cheyyam", "manglish"),
])
def test_detect_language(text, lang):
    assert detect_language(text) == lang


@pytest.mark.parametrize("text,intent", [
    ("ഇന്നത്തെ റിപ്പോർട്ട് തരൂ", "status_report"),
    ("site-one പരിശോധിക്കൂ", "run_check"),
    ("എന്തെങ്കിലും പ്രശ്നങ്ങൾ ഉണ്ടോ", "list_incidents"),
    ("seo report tharu", "seo_report"),
    ("innathe report tharu", "status_report"),
    ("site-one check cheyyu", "run_check"),
    ("prashnangal undo", "list_incidents"),
    ("prashnangall undo", "list_incidents"),       # misspelt Manglish (fuzzy)
    ("ബാക്കപ്പ് എടുക്കൂ", "backup"),
    ("what is broken on Billing App", "list_incidents"),
    ("help", "help"),
    # English words as Malayalam speech recognition writes them
    ("ചെക്ക് ചെയ്യൂ", "run_check"),
    ("ഒന്ന് ചെക്ക്", "run_check"),
    ("എന്തെങ്കിലും പ്രോബ്ലംസ് ഉണ്ടോ", "list_incidents"),
    ("ഇഷ്യൂസ് പറയൂ", "list_incidents"),
    ("ഹെൽപ്പ്", "help"),
    ("സമ്മറി തരൂ", "status_report"),
])
def test_intents(text, intent):
    assert parse_command(text, PROJECTS).name == intent


def test_project_extraction():
    assert parse_command("site-one check cheyyu", PROJECTS).project == "site-one"
    assert parse_command("report for Billing App", PROJECTS).project == "app-one"


def test_approve_requires_id_and_confirmation():
    no_id = parse_command("ചെയ്തോളൂ")
    assert no_id.name == "approve" and no_id.approval_id is None and no_id.confidence <= 0.5
    with_id = parse_command("approve APR-1A2B3C4D")
    assert with_id.approval_id == "APR-1A2B3C4D" and with_id.needs_confirmation


def test_reject_wins_over_approve():
    assert parse_command("venda, approve cheyyenda").name == "reject"


def test_unknown_is_unknown():
    assert parse_command("കാലാവസ്ഥ എങ്ങനെ").name == "unknown"


def test_metrics():
    assert cer("റിപ്പോർട്ട്", "റിപ്പോർട്ട്") == 0
    assert wer("innathe report tharu", "innathe report") == pytest.approx(1 / 3)
    assert 0 < cer("റിപ്പോർട്ട് തരൂ", "റിപ്പോട്ട് തരു") < 0.5


def test_status_not_tested_until_evaluated(tmp_path):
    status = voice_status(tmp_path / "voice")
    assert status["speech_to_text"].startswith("NOT TESTED")
    assert status["text_to_speech"].startswith("NOT TESTED")


def test_verified_needs_enough_samples_and_accuracy(tmp_path):
    good = SampleResult("a.wav", "report tharu", "report tharu", 0.0, 0.0, "status_report",
                        "status_report", 2.0)
    few = summarize("test", [good] * 5, tmp_path)
    assert few["verified"] is False                  # fewer than 30 samples
    many = summarize("test", [good] * 30, tmp_path)
    assert many["verified"] is True
    assert voice_status(tmp_path)["speech_to_text"].startswith("VERIFIED")
