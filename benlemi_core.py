#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""benlemi_core.py — deterministic Benlemi feed -> sklad:* tag logic."""
import re

# (min_weeks, max_weeks) -> sklad tag. Confirmed against main-product-customizable.liquid.
WINDOW_TO_TAG = {
    (1, 2):  "sklad:preorder1",
    (1, 3):  "sklad:preorder6",
    (2, 3):  "sklad:naceste",
    (2, 4):  "sklad:naceste1",
    (3, 4):  "sklad:preorder",
    (3, 5):  "sklad:preorder3",
    (4, 5):  "sklad:preorder7",
    (4, 6):  "sklad:preorder2",
    (5, 7):  "sklad:preorder8",
    (6, 8):  "sklad:preorder4",
    (8, 10): "sklad:preorder5",
    (1, 1):  "sklad:preorder1",   # Benlemi "1 weeks"
    (3, 6):  "sklad:preorder2",   # Benlemi "3 - 6 weeks" -> nearest 4-6
}
# Tags this automation is allowed to set/replace (the Benlemi week-window tags).
MANAGED_TAGS = set(WINDOW_TO_TAG.values())
# sklad tags that exist in the theme but are OUT of Benlemi feed scope (own stock).
NON_MANAGED_SKLAD = {"sklad:5-9days", "sklad:6-8days"}

# severity best->worst, for collapsing divergent variants if ever needed
TAG_ORDER = [WINDOW_TO_TAG[k] for k in sorted(WINDOW_TO_TAG, key=lambda mm: (mm[1], mm[0]))]
TAG_RANK = {t: i for i, t in enumerate(TAG_ORDER)}

_RANGE = re.compile(r"(\d+)\s*[-–]\s*(\d+)\s*week", re.I)
_SINGLE = re.compile(r"(\d+)\s*week", re.I)
_INSTOCK = ("in stock", "skladem", "in stock >5", "in stock <5")


def resolve_availability(in_stock_text, out_text):
    """
    Current availability comes from AVAILABILITY_OUT_OF_STOCK (authoritative in
    this feed); AVAILABILITY_IN_STOCK is a static "In stock" label on every item.
    Rules:
      OUT contains "In stock"  -> in stock now      -> remove sklad tag
      OUT has "X - Y weeks"     -> dispatch/produce  -> map (min,max) window
      "Ask us" / empty / other  -> flag (manual)
    ("Dispatch within..." and "Produce within..." both carry the week window.)
    """
    out = (out_text or "").strip()
    text = out if out else (in_stock_text or "").strip()
    low = text.lower()
    if low == "":
        return dict(state="flag", wmin=None, wmax=None, tag=None, note="empty availability")
    if "in stock" in low or low == "skladem" or "day" in low or "hod" in low:
        return dict(state="in_stock", wmin=None, wmax=None, tag=None, note="in stock -> remove sklad tag")

    m = _RANGE.search(text) or _SINGLE.search(text)
    if not m:
        return dict(state="flag", wmin=None, wmax=None, tag=None, note=f"unmapped availability: {text!r}")
    if m.re is _RANGE:
        wmin, wmax = int(m.group(1)), int(m.group(2))
    else:
        wmin = wmax = int(m.group(1))
    tag = WINDOW_TO_TAG.get((wmin, wmax))
    if not tag:
        return dict(state="flag", wmin=wmin, wmax=wmax, tag=None, note=f"window {wmin}-{wmax}w has no matching tag")
    return dict(state="dispatch", wmin=wmin, wmax=wmax, tag=tag, note="")


if __name__ == "__main__":
    for (mn, mx), tag in WINDOW_TO_TAG.items():
        assert resolve_availability("In stock", f"Dispatch within {mn} - {mx} weeks")["tag"] == tag
    assert resolve_availability("In stock", "")["state"] == "in_stock"
    assert resolve_availability("In stock", "Ask us")["state"] == "flag"
    print("core self-test OK:", len(WINDOW_TO_TAG), "windows + in_stock + flag")
