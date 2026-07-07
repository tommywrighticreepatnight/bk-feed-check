#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""benlemi_pipeline.py — load feed + Shopify products, decide target tag per product."""
import csv, re, urllib.request
import xml.etree.ElementTree as ET
from collections import OrderedDict
from benlemi_core import resolve_availability, NON_MANAGED_SKLAD, TAG_RANK

VENDOR_MATCH = "benlemi"


def _base_sku(skus):
    """Representative SKU for orientation: first variant SKU trimmed before ' size '."""
    if not skus:
        return ""
    return re.split(r"\s+size\s+", skus[0], flags=re.I)[0].strip().rstrip(",")
PART_MARKER = "part"


def load_feed(src):
    """URL or local path -> {ean: resolved_dict}."""
    if src.startswith(("http://", "https://")):
        with urllib.request.urlopen(src, timeout=60) as r:
            data = r.read()
    else:
        data = open(src, "rb").read()
    root = ET.fromstring(data)
    feed = {}
    def txt(el):
        return (el.text or "").strip() if el is not None else ""
    for item in root.iter("SHOPITEM"):
        variants = item.findall(".//VARIANT")
        if variants:                      # EAN + availability live per variant
            for v in variants:
                ean = txt(v.find("EAN"))
                if not ean:
                    continue
                feed[ean] = resolve_availability(txt(v.find("AVAILABILITY_IN_STOCK")),
                                                 txt(v.find("AVAILABILITY_OUT_OF_STOCK")))
        else:                             # simple item, EAN at top level
            ean = txt(item.find("EAN"))
            if ean:
                feed[ean] = resolve_availability(txt(item.find("AVAILABILITY_IN_STOCK")),
                                                 txt(item.find("AVAILABILITY_OUT_OF_STOCK")))
    return feed


def load_export(path):
    """Shopify products export CSV -> dict handle -> product record."""
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    by_handle = OrderedDict()
    for r in rows:
        by_handle.setdefault(r["Handle"], []).append(r)
    products = OrderedDict()
    for handle, rr in by_handle.items():
        vendor = next((r["Vendor"].strip() for r in rr if r["Vendor"].strip()), "")
        title = next((r["Title"].strip() for r in rr if r["Title"].strip()), "")
        tags_str = next((r["Tags"] for r in rr if r["Tags"].strip()), "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        eans = [r["Variant Barcode"].strip() for r in rr if r["Variant Barcode"].strip()]
        skus = [r["Variant SKU"].strip() for r in rr if r["Variant SKU"].strip()]
        sklad = [t for t in tags if t.startswith("sklad:")]
        products[handle] = dict(handle=handle, title=title, vendor=vendor, tags=tags,
                                eans=eans, sku=_base_sku(skus),
                                sklad_current=sklad[0] if sklad else "",
                                has_part=any(PART_MARKER in t for t in tags))
    return products


def human_window(res):
    """Short human string for the feed value."""
    if res["state"] == "in_stock":
        return "in stock"
    if res["state"] == "dispatch":
        return f"{res['wmin']}-{res['wmax']} weeks"
    return res["note"]


def decide(prod, feed):
    """
    Return dict: kind, target, detail, feed_str, matched.
    kind in: NO_CHANGE, CHANGE, SKIP_PART, SKIP_NO_FEED, SKIP_VENDOR,
             FLAG_OWNSTOCK, FLAG_DISAGREE, FLAG_MANUAL
    target: tag to set; "" = remove tag; None = n/a.
    """
    if VENDOR_MATCH not in prod["vendor"].lower():
        return dict(kind="SKIP_VENDOR", target=None, detail=f"vendor={prod['vendor']!r}",
                    feed_str="", matched=0)

    matched = [(e, feed[e]) for e in prod["eans"] if e in feed]
    m = len(matched)
    if not matched:
        return dict(kind="SKIP_NO_FEED", target=None, detail="no EAN in feed", feed_str="", matched=0)

    feed_vals = sorted({human_window(res) for _, res in matched})
    feed_str = feed_vals[0] if len(feed_vals) == 1 else "mixed: " + ", ".join(feed_vals)

    if prod["has_part"]:
        return dict(kind="SKIP_PART", target=None, detail="manual *part* override", feed_str=feed_str, matched=m)
    if prod["sklad_current"] in NON_MANAGED_SKLAD:
        return dict(kind="FLAG_OWNSTOCK", target=None,
                    detail=f"has {prod['sklad_current']} (own-stock, outside feed)", feed_str=feed_str, matched=m)

    targets, flags = set(), []
    for e, res in matched:
        if res["state"] == "in_stock":
            targets.add("")
        elif res["state"] == "dispatch":
            targets.add(res["tag"])
        else:
            flags.append(f"{e}:{res['note']}")

    if flags and not targets:
        return dict(kind="FLAG_MANUAL", target=None, detail="; ".join(flags), feed_str=feed_str, matched=m)
    if len(targets) > 1:
        worst = max((t for t in targets if t), key=lambda t: TAG_RANK.get(t, -1), default="")
        pretty = sorted(t or "(remove)" for t in targets)
        return dict(kind="FLAG_DISAGREE", target=None,
                    detail=f"variants differ {pretty} (worst={worst})", feed_str=feed_str, matched=m)

    target = targets.pop()
    cur = prod["sklad_current"]
    if target == cur:
        return dict(kind="NO_CHANGE", target=target, detail="in sync", feed_str=feed_str, matched=m)
    return dict(kind="CHANGE", target=target,
                detail=f"{cur or '(none)'} -> {target or '(remove)'}", feed_str=feed_str, matched=m)


def load_times_xlsx(path, barcode_col=3, time_col=34, sheet="Benlemi"):
    """benlemi_match.xlsx -> {barcode: resolved_dict}. Keyed by the BusyKids
    barcode (= Shopify Variant Barcode), value = resolved 'time of sending'.
    Same shape as load_feed output, so the rest of the pipeline is unchanged."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet]
    out = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not any(r):
            continue
        bar = str(r[barcode_col]).strip() if r[barcode_col] is not None else ""
        tos = str(r[time_col]).strip() if r[time_col] is not None else ""
        if bar:
            out[bar] = resolve_availability("", tos)
    return out
