#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
benlemi_state.py — turn per-product decisions into sheet rows with a status
state machine that survives across runs.

Human edits ONLY the `Status` column (pending -> done). Everything else is
tool-managed. The `Sig` column encodes *what change this row represents*; the
tool uses it to know whether a human's `done` still applies on the next run.

Transitions (per Handle):
  in sync (NO_CHANGE)         -> Status=ok,      Action=—
  has *part*                  -> Status=skip,    Action=— (manual override)
  change / flag needed:
     prior Status==done AND prior Sig==new Sig  -> Status=done, Action=—   (human handled it)
     otherwise                                  -> Status=pending|review, Action=<what to do>
When the feed changes the required tag, Sig changes, so a stale `done` flips
back to `pending` automatically.
"""
from datetime import datetime, timezone

HEADER = ["Handle", "Title", "Matched", "Feed", "Current", "Target",
          "Action", "Status", "Note", "Sig", "Checked"]

# statuses the tool sets; human may switch pending->done (or review->done)
_HUMAN_EDITABLE = {"pending", "review"}


def signature(dec):
    """Stable id of the change a row represents. Same target => same sig."""
    k = dec["kind"]
    if k == "CHANGE":
        return f"set:{dec['target'] or '(remove)'}"
    if k == "NO_CHANGE":
        return "ok"
    if k == "SKIP_PART":
        return "skip"
    return f"flag:{k}"          # FLAG_OWNSTOCK / FLAG_DISAGREE / FLAG_MANUAL


def _action_text(dec):
    k = dec["kind"]
    if k == "CHANGE":
        t = dec["target"]
        return "remove sklad tag" if t == "" else f"set {t}"
    if k == "FLAG_DISAGREE":
        return "review: variants differ -> manual 'part'"
    if k == "FLAG_OWNSTOCK":
        return "review: own-stock tag, decide manually"
    if k == "FLAG_MANUAL":
        return "review: unmapped availability"
    return "—"


def transition(dec, prior):
    """
    dec: decision dict from pipeline.decide()
    prior: dict of the existing sheet row for this Handle, or None
    returns (action, status, sig)
    """
    sig = signature(dec)
    k = dec["kind"]

    if k == "NO_CHANGE":
        return "—", "ok", sig
    if k == "SKIP_PART":
        return "— (has *part*)", "skip", sig

    # CHANGE or any FLAG_* => action is needed unless a human already cleared it
    prior_status = (prior or {}).get("Status", "").strip().lower()
    prior_sig = (prior or {}).get("Sig", "").strip()
    if prior_status == "done" and prior_sig == sig:
        return "—", "done", sig          # human handled this exact change; stay quiet

    status = "pending" if k == "CHANGE" else "review"
    return _action_text(dec), status, sig


def build_rows(decisions, prior_by_handle, include_kinds=None):
    """
    decisions: list of (product, decision) tuples
    prior_by_handle: {handle: prior_row_dict}
    include_kinds: set of decision kinds to include in the sheet
                   (default: everything that touches the feed, i.e. not SKIP_NO_FEED/SKIP_VENDOR)
    returns (rows, counts)
    """
    if include_kinds is None:
        include_kinds = {"CHANGE", "NO_CHANGE", "SKIP_PART",
                         "FLAG_OWNSTOCK", "FLAG_DISAGREE", "FLAG_MANUAL"}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    rows, counts = [], {}
    for prod, dec in decisions:
        counts[dec["kind"]] = counts.get(dec["kind"], 0) + 1
        if dec["kind"] not in include_kinds:
            continue
        action, status, sig = transition(dec, prior_by_handle.get(prod["handle"]))
        rows.append({
            "Handle": prod["handle"],
            "Title": prod["title"],
            "Matched": dec["matched"],
            "Feed": dec["feed_str"],
            "Current": prod["sklad_current"] or "(none)",
            "Target": (dec["target"] if dec["target"] is not None else ""),
            "Action": action,
            "Status": status,
            "Note": dec["detail"],
            "Sig": sig,
            "Checked": now,
        })
    # stable, useful ordering: pending first, then review, done, ok, skip
    order = {"pending": 0, "review": 1, "done": 2, "ok": 3, "skip": 4}
    rows.sort(key=lambda r: (order.get(r["Status"], 9), r["Handle"]))
    return rows, counts
