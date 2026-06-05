#!/usr/bin/env python3
"""telemetry_summary.py — print aggregate stats from the JSONL telemetry log.

Usage:
    python3 ai_engine/scripts/telemetry_summary.py                     # summary
    python3 ai_engine/scripts/telemetry_summary.py --last 50           # last 50 pipelines only
    python3 ai_engine/scripts/telemetry_summary.py --file /path.jsonl  # custom log
    python3 ai_engine/scripts/telemetry_summary.py --raw fixer.fire    # dump raw events

The intent: answer "which fixers actually fire" and "what's our image bind
rate" without writing a query. Run after ~50 generations to get signal —
small N (≤5) is noisy and shouldn't drive refactor decisions.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

_DEFAULT_PATH = "/tmp/lucid_telemetry.jsonl"


def _load(path: str, last: int | None = None) -> list[dict]:
    if not os.path.exists(path):
        print(f"telemetry file not found: {path}", file=sys.stderr)
        sys.exit(1)
    events = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if last is None:
        return events
    # Take the last N pipeline.start project_ids, return all events for those.
    starts = [e for e in events if e.get("event") == "pipeline.start"]
    if not starts:
        return events
    keep_ids = {s.get("project_id") for s in starts[-last:] if s.get("project_id")}
    return [e for e in events if e.get("project_id") in keep_ids or not e.get("project_id")]


def _section_header(title: str) -> None:
    print()
    print(title)
    print("=" * len(title))


def _summarize(events: list[dict]) -> None:
    starts = [e for e in events if e.get("event") == "pipeline.start"]
    completes = [e for e in events if e.get("event") == "pipeline.complete"]
    fixer_events = [e for e in events if e.get("event") == "fixer.fire"]
    fixer_errors = [e for e in events if e.get("event") == "fixer.error"]
    binder_events = [e for e in events if e.get("event") == "image_binder.summary"]
    section_events = [e for e in events if e.get("event") == "section.generated"]
    fallback_events = [e for e in events if e.get("event") == "section.fallback"]

    _section_header("PIPELINE")
    print(f"  starts            : {len(starts)}")
    print(f"  completes (success): {len(completes)}")
    print(f"  inferred failures : {len(starts) - len(completes)}")
    if completes:
        durations = [e.get("duration_sec", 0) for e in completes if isinstance(e.get("duration_sec"), (int, float))]
        if durations:
            print(f"  duration (sec)    : avg={statistics.mean(durations):.1f}  median={statistics.median(durations):.1f}  p95={sorted(durations)[int(len(durations)*0.95)-1]:.1f}")
        sec_counts = [e.get("section_count", 0) for e in completes if isinstance(e.get("section_count"), int)]
        if sec_counts:
            print(f"  sections per gen  : avg={statistics.mean(sec_counts):.1f}  min={min(sec_counts)}  max={max(sec_counts)}")

    _section_header(f"FIXERS  ({len(fixer_events)} fires across {len(completes) or len(starts) or 1} pipelines)")
    if fixer_events:
        per_fixer_fires = defaultdict(int)
        per_fixer_modified = defaultdict(int)
        per_fixer_runs = defaultdict(int)
        for e in fixer_events:
            name = e.get("name", "?")
            per_fixer_runs[name] += 1
            mod = e.get("files_modified", 0) or 0
            if mod > 0:
                per_fixer_fires[name] += 1
                per_fixer_modified[name] += mod
        rows = []
        for name in sorted(per_fixer_runs.keys()):
            runs = per_fixer_runs[name]
            fires = per_fixer_fires[name]
            total_mod = per_fixer_modified[name]
            fire_rate = (fires / runs * 100) if runs else 0
            rows.append((fire_rate, name, runs, fires, total_mod))
        rows.sort(reverse=True)
        print(f"  {'fire-rate':>9}  {'fixer':<42} {'runs':>5} {'fires':>5} {'files':>6}")
        for fr, name, runs, fires, mods in rows:
            tag = ""
            if fr == 0:
                tag = " ← DEAD (candidate for prompt rule removal)"
            elif fr >= 80:
                tag = " ← LOAD-BEARING (prompt rule is failing — investigate)"
            print(f"  {fr:>8.0f}%  {name:<42} {runs:>5} {fires:>5} {mods:>6}{tag}")

    if fixer_errors:
        _section_header(f"FIXER ERRORS  ({len(fixer_errors)})")
        err_counts = Counter(e.get("name", "?") for e in fixer_errors)
        for name, n in err_counts.most_common():
            print(f"  {name:<42} {n}")

    _section_header(f"IMAGE BINDER  ({len(binder_events)} runs)")
    if binder_events:
        req = sum(e.get("requested", 0) for e in binder_events)
        bound = sum(e.get("bound", 0) for e in binder_events)
        unbound = sum(e.get("unbound", 0) for e in binder_events)
        geo = sum(e.get("geo_rejected", 0) for e in binder_events)
        subj = sum(e.get("subject_rejected", 0) for e in binder_events)
        retry = sum(e.get("retry_used", 0) for e in binder_events)
        empty = sum(e.get("search_empty", 0) for e in binder_events)
        fallback = sum(e.get("fallback_to_raw", 0) for e in binder_events)
        if req:
            print(f"  bind rate         : {bound}/{req} ({bound/req*100:.1f}%)  unbound={unbound}")
        print(f"  retries used      : {retry}  (initial-search-empty events: {empty})")
        print(f"  geo rejections    : {geo}  (clashed with locked country)")
        print(f"  subject rejections: {subj}  (flag/landmark/logo/scrabble etc.)")
        print(f"  fallback-to-raw   : {fallback}  (every filtered candidate clashed)")

    _section_header(f"SECTIONS  ({len(section_events)} generated, {len(fallback_events)} fell back)")
    if section_events:
        type_counts = Counter(e.get("section_type", "?") for e in section_events)
        for t, n in type_counts.most_common():
            print(f"  {t:<24} {n}")
        retried = [e for e in section_events if e.get("attempts", 1) > 1]
        if retried:
            print(f"  retried on 2nd attempt: {len(retried)}/{len(section_events)} ({len(retried)/len(section_events)*100:.1f}%)")
    if fallback_events:
        print()
        print(f"  FALLBACK reasons:")
        reason_counts = Counter((e.get("reason") or "?")[:80] for e in fallback_events)
        for r, n in reason_counts.most_common(5):
            print(f"    {n:>3}× {r}")


def _raw(events: list[dict], event_name: str) -> None:
    for e in events:
        if e.get("event") == event_name:
            print(json.dumps(e, default=str))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", "-f", default=os.environ.get("TELEMETRY_FILE", _DEFAULT_PATH),
                    help=f"telemetry JSONL file (default: $TELEMETRY_FILE or {_DEFAULT_PATH})")
    ap.add_argument("--last", "-n", type=int, default=None,
                    help="restrict to the last N pipeline runs")
    ap.add_argument("--raw", "-r", default="",
                    help="dump raw events of this type (e.g. --raw fixer.fire)")
    args = ap.parse_args()

    events = _load(args.file, last=args.last)
    if not events:
        print("(no events yet)")
        return

    if args.raw:
        _raw(events, args.raw)
    else:
        _summarize(events)


if __name__ == "__main__":
    main()
