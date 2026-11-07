"""Language detection for Malayalam script, Manglish (Malayalam in Latin
letters) and English."""

from __future__ import annotations

import re
import unicodedata

MALAYALAM_RANGE = (0x0D00, 0x0D7F)
_ZW = dict.fromkeys(map(ord, "‌‍﻿"), None)  # ZWNJ, ZWJ, BOM

# Frequent Manglish words that are not English words.
MANGLISH_MARKERS = {
    "ente", "enikku", "ningal", "njan", "njaan", "ningalude", "cheyyu", "cheyyuka", "cheyth",
    "cheytho", "cheyyamo", "tharu", "tharoo", "tharumo", "venda", "venam", "vendath", "und",
    "undo", "illa", "alla", "aanu", "ano", "entha", "enthu", "enthaanu", "engane", "evide",
    "innathe", "innu", "nale", "ippo", "ippol", "parayu", "nokku", "nokkuka", "kaanikku",
    "sheriyakku", "prashnam", "prashnangal", "sahayam", "sthithi", "onnu", "ellam", "ella",
    "kittiyo", "kitti", "mathi", "pinne", "athu", "ithu", "sammatham", "parishodhikku",
    # short / alternative spellings people actually type
    "nigal", "ningalu", "ninghal", "aran", "aara", "aaranu", "aaru", "enthokke", "enthanu",
    "enthaa", "parayamo", "ariyamo", "sugamano", "sukhamano", "alle", "ille", "pattumo",
    "cheyyam", "ninte", "nee", "enne", "namukku", "eppol", "evideya", "enthinu", "kollam",
    "nannayi", "manassilayo", "manasilayo", "paranju", "tharamo", "kaanikkamo",
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(_ZW).lower()
    text = re.sub(r"[^\w\sഀ-ൿ-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def malayalam_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha() or MALAYALAM_RANGE[0] <= ord(ch) <= MALAYALAM_RANGE[1]]
    if not letters:
        return 0.0
    ml = sum(MALAYALAM_RANGE[0] <= ord(ch) <= MALAYALAM_RANGE[1] for ch in letters)
    return ml / len(letters)


def detect_language(text: str) -> str:
    """Return 'ml' (Malayalam script), 'manglish', or 'en'."""
    if malayalam_ratio(text) >= 0.3:
        return "ml"
    tokens = set(normalize(text).split())
    if tokens & MANGLISH_MARKERS:
        return "manglish"
    return "en"
