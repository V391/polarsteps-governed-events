"""Schema change process: classify a tracking plan change before it merges.

  additive        safe: new event, new optional property, new enum value
  breaking        old app versions or downstream models will break: removed event or property,
                  type change, property made required, new required property, enum value removed
  privacy_review  needs Legal sign off: a property's pii class loosened, or a new destination added

Usage: python src/plan_diff.py OLD.yml NEW.yml   (exit 1 on breaking or privacy_review, for CI)
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

STRICTNESS = {"none": 0, "free_text": 1, "identifier": 2, "location": 3, "direct": 4}


def diff(old: dict, new: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    oe, ne = old["events"], new["events"]
    for name in sorted(set(oe) - set(ne)):
        out.append(("breaking", f"event removed: {name}"))
    for name in sorted(set(ne) - set(oe)):
        out.append(("additive", f"event added: {name} (owner {ne[name].get('owner', '?')})"))
        if not ne[name].get("owner"):
            out.append(("breaking", f"event {name} has no owner"))
    for name in sorted(set(oe) & set(ne)):
        o, n = oe[name], ne[name]
        for d in sorted(set(n.get("destinations", [])) - set(o.get("destinations", []))):
            out.append(("privacy_review", f"{name}: new destination {d}"))
        op, np_ = o["properties"], n["properties"]
        for p in sorted(set(op) - set(np_)):
            out.append(("breaking", f"{name}.{p} removed"))
        for p in sorted(set(np_) - set(op)):
            kind = "breaking" if np_[p].get("required") else "additive"
            out.append((kind, f"{name}.{p} added ({'required' if np_[p].get('required') else 'optional'}, pii {np_[p]['pii']})"))
        for p in sorted(set(op) & set(np_)):
            a, b = op[p], np_[p]
            if a["type"] != b["type"]:
                out.append(("breaking", f"{name}.{p} type {a['type']} -> {b['type']}"))
            if b.get("required") and not a.get("required"):
                out.append(("breaking", f"{name}.{p} made required"))
            for v in sorted(set(a.get("enum", [])) - set(b.get("enum", []))):
                out.append(("breaking", f"{name}.{p} enum value removed: {v}"))
            for v in sorted(set(b.get("enum", [])) - set(a.get("enum", []))):
                out.append(("additive", f"{name}.{p} enum value added: {v}"))
            if STRICTNESS[b["pii"]] < STRICTNESS[a["pii"]]:
                out.append(("privacy_review", f"{name}.{p} pii {a['pii']} -> {b['pii']}"))
    return out


def main(argv: list[str]) -> int:
    old, new = (yaml.safe_load(Path(p).read_text(encoding="utf-8")) for p in argv[:2])
    changes = diff(old, new)
    for kind in ("breaking", "privacy_review", "additive"):
        for k, msg in changes:
            if k == kind:
                print(f"[{kind}] {msg}")
    if not changes:
        print("no changes")
    blocking = [c for c in changes if c[0] != "additive"]
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
