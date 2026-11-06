"""Measure Malayalam speech recognition on YOUR recordings.

Manifest: CSV with columns  audio,reference,expected_intent
  audio            path to a .wav/.ogg/.mp3 recording of you speaking
  reference        exactly what you said (Malayalam script or Manglish)
  expected_intent  the command intent it should map to (see language/commands.py)

Metrics:
  CER  character error rate (main metric for Malayalam, whose long
       agglutinated words make WER harsh)
  WER  word error rate
  intent accuracy  share of recordings mapped to the right command. This is
       what decides whether voice control is usable.
"""

from __future__ import annotations

import csv
import json
import statistics
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from ..language.commands import parse_command
from ..language.detect import normalize

# A configuration is reported as VERIFIED only if it meets all of these.
THRESHOLDS = {"intent_accuracy_min": 0.90, "cer_max": 0.25, "samples_min": 30}


def _edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _prep(text: str) -> str:
    return normalize(unicodedata.normalize("NFC", text))


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = list(_prep(reference).replace(" ", "")), list(_prep(hypothesis).replace(" ", ""))
    return _edit_distance(ref, hyp) / max(len(ref), 1)


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = _prep(reference).split(), _prep(hypothesis).split()
    return _edit_distance(ref, hyp) / max(len(ref), 1)


@dataclass
class SampleResult:
    audio: str
    reference: str
    hypothesis: str
    cer: float
    wer: float
    expected_intent: str
    got_intent: str
    seconds: float


def load_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        missing = {"audio", "reference", "expected_intent"} - set(row)
        if missing:
            raise ValueError(f"Manifest missing columns: {missing}")
        audio = Path(row["audio"])
        if not audio.is_absolute():
            row["audio"] = str((path.parent / audio).resolve())
    return rows


def evaluate(stt, manifest: Path, out_dir: Path) -> dict:
    rows = load_manifest(manifest)
    samples: list[SampleResult] = []
    for row in rows:
        transcript = stt.transcribe(Path(row["audio"]))
        intent = parse_command(transcript.text).name
        samples.append(SampleResult(row["audio"], row["reference"], transcript.text,
                                    cer(row["reference"], transcript.text),
                                    wer(row["reference"], transcript.text),
                                    row["expected_intent"], intent, transcript.seconds))
    return summarize(stt.name, samples, out_dir)


def summarize(provider: str, samples: list[SampleResult], out_dir: Path) -> dict:
    n = len(samples)
    intent_acc = sum(s.got_intent == s.expected_intent for s in samples) / n if n else 0.0
    mean_cer = statistics.mean(s.cer for s in samples) if n else 1.0
    report = {
        "provider": provider,
        "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "samples": n,
        "intent_accuracy": round(intent_acc, 3),
        "mean_cer": round(mean_cer, 3),
        "mean_wer": round(statistics.mean(s.wer for s in samples), 3) if n else 1.0,
        "median_seconds": round(statistics.median(s.seconds for s in samples), 2) if n else None,
        "thresholds": THRESHOLDS,
        "verified": n >= THRESHOLDS["samples_min"]
                    and intent_acc >= THRESHOLDS["intent_accuracy_min"]
                    and mean_cer <= THRESHOLDS["cer_max"],
        "details": [asdict(s) for s in samples],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"stt-eval-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def voice_status(out_dir: Path) -> dict:
    """What has actually been verified on this machine."""
    reports = sorted(out_dir.glob("stt-eval-*.json")) if out_dir.exists() else []
    tts_ratings = sorted(out_dir.glob("tts-rating-*.json")) if out_dir.exists() else []
    latest = json.loads(reports[-1].read_text(encoding="utf-8")) if reports else None
    return {
        "speech_to_text": ("NOT TESTED: no evaluation on your recordings yet" if not latest else
                           f"{'VERIFIED' if latest['verified'] else 'TESTED, BELOW THRESHOLD'}: "
                           f"{latest['provider']} intent accuracy {latest['intent_accuracy']:.0%}, "
                           f"CER {latest['mean_cer']:.0%}, {latest['samples']} samples"),
        "text_to_speech": ("NOT TESTED: no listening ratings recorded yet" if not tts_ratings
                           else f"{len(tts_ratings)} rating session(s) recorded"),
    }
