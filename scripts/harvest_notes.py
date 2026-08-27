#!/usr/bin/env python3
"""
Harvest DealCloud ResearchNote payloads that the MCP tool has already returned,
and run the conservative parser over each.

Why this exists: the DealCloud MCP tool hands its result to the model, not to a
file. Research note bodies are large (one real note is 95k characters), so
re-emitting them into a file by hand is slow and error-prone. Claude Code
already persists every tool result -- inline in the session transcript, or in a
tool-results/ file when the payload is oversized. This scans both, pulls out any
{"row": {"EntryId": ..., "Body": ...}} payload it finds, and parses it.

Weekly flow:
    1. Claude calls get_dealcloud_entity_by_id for each fund's latest note.
    2. python scripts/harvest_notes.py --session-dir <dir> --transcript <jsonl>
    3. Merge cache/notes_parsed.json into the report manifest.

Nothing here is guessed: a note that yields no weights is recorded with the
parser's own reason string.
"""

import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dealcloud_note_parser import parse_note  # noqa: E402

ROOT = os.path.dirname(HERE)
NOTES_DIR = os.path.join(ROOT, "cache", "notes")
PARSED = os.path.join(ROOT, "cache", "notes_parsed.json")


def _walk_strings(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_strings(v)
    elif isinstance(node, str):
        yield node


def _payloads_from_text(text):
    """Yield dicts that look like a ResearchNote row payload."""
    if "Body" not in text or "EntryId" not in text:
        return
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            start = text.find("{", start + 1)
            continue
        row = obj.get("row") if isinstance(obj, dict) else None
        if isinstance(row, dict) and "Body" in row and "EntryId" in row:
            yield obj
        return


def harvest(sources):
    found = {}
    for path in sources:
        try:
            raw = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue

        candidates = [raw]
        # Transcript files are JSON-lines whose tool results nest the payload
        # as an escaped string; unwrap one level.
        if path.endswith(".jsonl"):
            candidates = []
            for line in raw.splitlines():
                if "EntryId" not in line or "Body" not in line:
                    continue
                try:
                    candidates.extend(_walk_strings(json.loads(line)))
                except ValueError:
                    continue
        elif path.endswith(".json"):
            try:
                candidates.extend(_walk_strings(json.loads(raw)))
            except ValueError:
                pass

        for text in candidates:
            for obj in _payloads_from_text(text):
                row = obj["row"]
                eid = row.get("EntryId")
                body = row.get("Body") or ""
                prev = found.get(eid)
                if prev is None or len(body) > len(prev["row"].get("Body") or ""):
                    found[eid] = obj
    return found


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--transcript", action="append", default=[],
                   help="Session .jsonl transcript (repeatable)")
    p.add_argument("--session-dir", action="append", default=[],
                   help="Directory of persisted tool-results (repeatable)")
    p.add_argument("--notes-dir", default=NOTES_DIR,
                   help="Also read/write standalone note JSON files here")
    a = p.parse_args()

    sources = list(a.transcript)
    for d in a.session_dir:
        sources += sorted(glob.glob(os.path.join(d, "*.txt")))
        sources += sorted(glob.glob(os.path.join(d, "*.json")))
    if os.path.isdir(a.notes_dir):
        sources += sorted(glob.glob(os.path.join(a.notes_dir, "*.json")))

    found = harvest(sources)
    os.makedirs(a.notes_dir, exist_ok=True)

    results = {}
    for eid, obj in sorted(found.items()):
        row = obj["row"]
        with open(os.path.join(a.notes_dir, f"{eid}.json"), "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        subject = row.get("Subject")
        if isinstance(subject, dict):
            subject = subject.get("name")
        as_of = (row.get("AsOfDate") or "")[:10] or None
        parsed = parse_note(row.get("Body") or "", subject, as_of)
        results[str(eid)] = parsed
        status = (
            f"FOUND {len(parsed['holdings'])} rows via {parsed.get('method')}"
            if parsed["found"] else "no weights"
        )
        print(f"  {eid}  {str(subject)[:52]:<52} {as_of}  {status}", file=sys.stderr)

    with open(PARSED, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"\n{len(results)} notes harvested -> {PARSED}", file=sys.stderr)
    print(json.dumps({k: {"found": v["found"], "method": v.get("method"),
                          "as_of": v.get("as_of"), "reason": v.get("reason"),
                          "holdings": v["holdings"]}
                      for k, v in results.items()}, indent=2))


if __name__ == "__main__":
    main()
