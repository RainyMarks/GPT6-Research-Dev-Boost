"""Check a dataset manifest for split leakage, duplicates, temporal overlap and shortcut attributes.

Usage:
  python -X utf8 check_dataset.py manifest.csv [--split split] [--label label]
      [--group user --group repo] [--path path --root DIR] [--text content]
      [--near question] [--time created_at] [--attr source --attr length] [--order train,val,test]

The manifest is a CSV with one row per sample: an image, document, code file, query, user session,
flow, trace ... Checks:
  * rows per split (and per label with --label); empty split or label cells; labels unseen in training;
  * group leakage: a value of any --group column (user, author, session, document, repository,
    prompt template, source item, host, collection batch ...) that appears in more than one split;
  * exact duplicates across splits or with conflicting labels: identical file contents (--path) or
    identical text after lowercasing and collapsing whitespace (--text);
  * near duplicates across splits (--near): paraphrased text, lightly edited code. Rows are compared by
    IDF-weighted overlap of their word tokens, so shared rare tokens (names, identifiers, numbers) count
    and shared boilerplate does not; example pairs are printed for a manual check of the threshold;
  * temporal overlap (--time): evaluation rows that are not newer than the newest training row;
  * shortcuts (--attr, needs --label): how well one attribute (source, format, length, resolution,
    collection batch, annotator ...) predicts the label. A majority-vote rule is fitted on the first
    split of --order and scored with balanced accuracy on the other splits.
Media near duplicates (re-encoded images, audio, video) need perceptual hashing; write its cluster IDs
into a column and pass that column as --group.
Leakage, exact duplicates and broken rows are FAIL (exit code 1); near duplicates, temporal overlap and
shortcuts are WARN.
"""
import argparse
import bisect
import csv
import hashlib
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

KNOWN_ORDER = ["train", "training", "val", "valid", "validation", "dev", "test", "testing"]
counts = Counter()


def report(level, message):
    counts[level] += 1
    print(f"[{level}] {message}")


def rows_hint(numbers, limit=5):
    shown = ", ".join(str(n) for n in numbers[:limit])
    return shown + (" ..." if len(numbers) > limit else "")


def split_order(rows, split_col, given):
    present = sorted({r[split_col] for r in rows})
    if given:
        order = [s for s in given.split(",") if s in present]
        return order + [s for s in present if s not in order]
    return sorted(present, key=lambda s: KNOWN_ORDER.index(s.lower()) if s.lower() in KNOWN_ORDER else len(KNOWN_ORDER))


def check_splits(rows, label_col, split_col, order):
    print("\n== Splits" + (" and labels" if label_col else ""))
    width = max(len(s) for s in order + ["split"])
    labels = sorted({r[label_col] for r in rows}) if label_col else []
    table = defaultdict(Counter)
    for r in rows:
        table[r[split_col]][r[label_col] if label_col else ""] += 1
    print("  " + "split".ljust(width) + "  " + "n".rjust(8) + "".join(l.rjust(12) for l in labels))
    for s in order:
        n = sum(table[s].values())
        cells = "".join(f"{table[s][l]} ({table[s][l] / n:.0%})".rjust(12) for l in labels)
        print("  " + s.ljust(width) + "  " + str(n).rjust(8) + cells)
    for s in order[1:] if label_col else []:
        missing = sorted(set(table[s]) - set(table[order[0]]))
        if missing:
            report("WARN", f"labels in '{s}' never seen in '{order[0]}': {', '.join(missing)}")


def check_leakage(rows, keys, split_col, name):
    splits_of = defaultdict(set)
    for r, key in zip(rows, keys):
        if key:
            splits_of[key].add(r[split_col])
    leaked = {k: v for k, v in splits_of.items() if len(v) > 1}
    if not leaked:
        report("OK", f"{name}: no value spans more than one split ({len(splits_of)} distinct values)")
        return
    affected = sum(1 for key in keys if key in leaked)
    examples = "; ".join(f"{k[:40]} -> {', '.join(sorted(v))}" for k, v in list(leaked.items())[:3])
    report("FAIL", f"{name}: {len(leaked)} values span several splits, {affected} rows affected. e.g. {examples}")


def file_keys(rows, path_col, root):
    keys, missing = [], []
    for i, r in enumerate(rows):
        full = r[path_col] if os.path.isabs(r[path_col]) else os.path.join(root, r[path_col])
        if not os.path.isfile(full):
            missing.append(i + 2)
            keys.append(None)
            continue
        digest = hashlib.sha256()
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        keys.append(digest.hexdigest())
    if missing:
        report("FAIL", f"{len(missing)} files not found under {root} (manifest rows {rows_hint(missing)})")
    print(f"  hashed {len(keys) - len(missing)} files")
    return keys


def text_keys(rows, text_col):
    keys = [hashlib.sha1(" ".join(r[text_col].lower().split()).encode("utf-8")).hexdigest() if r[text_col].strip() else None
            for r in rows]
    empty = [i + 2 for i, k in enumerate(keys) if k is None]
    if empty:
        report("WARN", f"{len(empty)} rows have empty {text_col} (manifest rows {rows_hint(empty)})")
    return keys


def check_duplicates(rows, keys, label_col, split_col, name):
    check_leakage(rows, keys, split_col, name)
    groups = defaultdict(list)
    for i, key in enumerate(keys):
        if key:
            groups[key].append(i)
    if label_col:
        conflicting = [g for g in groups.values() if len({rows[i][label_col] for i in g}) > 1]
        if conflicting:
            rows_list = [i + 2 for g in conflicting for i in g]
            report("FAIL", f"{name}: {len(conflicting)} duplicate sets carry different labels (manifest rows {rows_hint(rows_list)})")
    within = sum(len(g) - 1 for g in groups.values() if len(g) > 1 and len({rows[i][split_col] for i in g}) == 1)
    if within:
        report("WARN", f"{name}: {within} extra copies inside a single split")


def check_near(rows, col, split_col, label_col, threshold, max_df):
    toks = [set(re.findall(r"\w+", r[col].lower())) for r in rows]
    df = Counter(t for ts in toks for t in ts)
    idf = {t: math.log(len(rows) / c) for t, c in df.items()}
    limit = max_df or max(2, len(rows) // 100)
    index = defaultdict(list)
    for i, ts in enumerate(toks):
        for t in ts:
            if df[t] <= limit:
                index[t].append(i)
    scores = {}
    for ids in index.values():
        for a, i in enumerate(ids):
            for j in ids[a + 1:]:
                if rows[i][split_col] == rows[j][split_col] or (i, j) in scores:
                    continue
                union = sum(idf[t] for t in toks[i] | toks[j])
                scores[(i, j)] = sum(idf[t] for t in toks[i] & toks[j]) / union if union else 0.0
    best = defaultdict(float)
    for (i, j), v in scores.items():
        for k in (i, j):
            best[k] = max(best[k], v)
    ladder = ", ".join(f">={t:.1f}: {sum(v >= t for v in best.values())}" for t in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9))
    print(f"  rows with a cross-split match at each threshold ({ladder}); a gap in these counts marks a usable threshold")
    hits = sorted(((v, k) for k, v in scores.items() if v >= threshold), reverse=True)
    if not hits:
        report("OK", f"no cross-split pair scores >= {threshold} ({len(scores)} candidate pairs compared)")
        return
    involved = {i for _, pair in hits for i in pair}
    report("WARN", f"{len(hits)} cross-split pairs score >= {threshold}, {len(involved)} rows involved; "
                   "check the examples below by hand before trusting the threshold")
    for score, (i, j) in hits[:3] + hits[-2:] if len(hits) > 5 else hits:
        print(f"    {score:.2f}  row {i + 2} ({rows[i][split_col]}): {rows[i][col][:70]!r}")
        print(f"          row {j + 2} ({rows[j][split_col]}): {rows[j][col][:70]!r}")
    if label_col:
        conflicting = sum(1 for _, (i, j) in hits if rows[i][label_col] != rows[j][label_col])
        if conflicting:
            report("WARN", f"{conflicting} of these pairs carry different labels")


def parse_time(value):
    try:
        return float(value)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def check_time(rows, split_col, time_col, order):
    print(f"\n== Temporal order ({time_col})")
    times, bad = defaultdict(list), []
    for i, r in enumerate(rows):
        t = parse_time(r[time_col]) if r[time_col] else None
        if t is None:
            bad.append(i + 2)
        else:
            times[r[split_col]].append((t, r[time_col]))
    if bad:
        report("WARN", f"{len(bad)} rows have an empty or unparseable time (manifest rows {rows_hint(bad)}); they are skipped")
    fit = order[0]
    if not times[fit]:
        report("WARN", f"no parseable times in '{fit}'")
        return
    newest = max(times[fit])[0]
    for s in order:
        if times[s]:
            print(f"  {s}: {min(times[s])[1]} .. {max(times[s])[1]}")
    for s in order[1:]:
        older = sum(1 for t, _ in times[s] if t <= newest)
        if older:
            report("WARN", f"{older}/{len(times[s])} rows of '{s}' are not newer than the newest '{fit}' row; "
                           "a time-ordered split keeps every evaluation sample after training")
        else:
            report("OK", f"every '{s}' row is newer than '{fit}'")


def as_numbers(values):
    nums = []
    for v in values:
        try:
            nums.append(float(v))
        except ValueError:
            return None
    return nums


def balanced_accuracy(pairs):
    per_label = defaultdict(lambda: [0, 0])
    for truth, pred in pairs:
        per_label[truth][1] += 1
        per_label[truth][0] += truth == pred
    return sum(hit / n for hit, n in per_label.values()) / len(per_label)


def check_shortcuts(rows, label_col, split_col, attrs, order, threshold):
    fit = order[0]
    labels = sorted({r[label_col] for r in rows})
    print(f"\n== Shortcut attributes (majority rule fitted on '{fit}', balanced accuracy, chance {1 / len(labels):.2f})")
    fit_rows = [r for r in rows if r[split_col] == fit]
    eval_splits = order[1:] or [fit]
    for attr in attrs:
        present = [r for r in rows if r[attr] != ""]
        if len(present) < len(rows):
            print(f"  {attr}: {len(rows) - len(present)} rows without a value are ignored")
        nums = as_numbers([r[attr] for r in present])
        if nums is not None and len(set(nums)) > 20:
            train_vals = sorted(float(r[attr]) for r in fit_rows if r[attr] != "")
            if not train_vals:
                report("WARN", f"{attr}: no values in '{fit}', cannot fit a rule")
                continue
            edges = sorted({train_vals[int(len(train_vals) * q / 10)] for q in range(1, 10)})
            key = lambda r: str(bisect.bisect_right(edges, float(r[attr])))
            by_label = defaultdict(list)
            for r in present:
                by_label[r[label_col]].append(float(r[attr]))
            detail = " | ".join(f"{l}: median {sorted(v)[len(v) // 2]:g}" for l, v in sorted(by_label.items()))
        else:
            key = lambda r: r[attr]
            by_label = defaultdict(Counter)
            for r in present:
                by_label[r[label_col]][r[attr]] += 1
            detail = " | ".join(
                f"{l}: " + ", ".join(f"{v} {c / sum(cnt.values()):.0%}" for v, c in cnt.most_common(3))
                for l, cnt in sorted(by_label.items()))
        votes = defaultdict(Counter)
        for r in fit_rows:
            if r[attr] != "":
                votes[key(r)][r[label_col]] += 1
        fallback = Counter(r[label_col] for r in fit_rows).most_common(1)[0][0]
        rule = {k: c.most_common(1)[0][0] for k, c in votes.items()}
        scores = []
        for s in eval_splits:
            pairs = [(r[label_col], rule.get(key(r), fallback)) for r in present if r[split_col] == s]
            if pairs:
                scores.append((s, balanced_accuracy(pairs)))
        text = ", ".join(f"{s} {acc:.2f}" for s, acc in scores) + ("  (in-sample: only one split)" if not order[1:] else "")
        worst = max((acc for _, acc in scores), default=0)
        report("WARN" if worst >= threshold else "OK", f"{attr}: {text}   {detail}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest")
    parser.add_argument("--split", default="split")
    parser.add_argument("--label", help="categorical target column; omit for unlabeled data")
    parser.add_argument("--group", action="append", default=[], help="column whose values must stay in one split")
    parser.add_argument("--path", help="column with file paths; identical file contents are compared")
    parser.add_argument("--root", help="directory that relative paths start from (default: the manifest's folder)")
    parser.add_argument("--text", action="append", default=[], help="text column compared after normalization")
    parser.add_argument("--near", action="append", default=[], help="text column searched for near duplicates")
    parser.add_argument("--near-threshold", type=float, default=0.5)
    parser.add_argument("--near-max-df", type=int, help="ignore tokens in more rows than this (default 1%% of rows)")
    parser.add_argument("--time", help="column with creation or capture time (ISO 8601 or a number)")
    parser.add_argument("--attr", action="append", default=[], help="attribute to test as a label shortcut")
    parser.add_argument("--order", help="comma-separated split order, training split first")
    parser.add_argument("--shortcut-threshold", type=float, default=0.75)
    args = parser.parse_args()
    if args.attr and not args.label:
        sys.exit("--attr tests whether an attribute predicts the label, so it needs --label")

    with open(args.manifest, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        columns = reader.fieldnames or []
    wanted = [args.split, *args.group, *args.text, *args.near, *args.attr] + [c for c in (args.label, args.path, args.time) if c]
    unknown = [c for c in wanted if c not in columns]
    if unknown:
        sys.exit(f"columns not in {args.manifest}: {', '.join(unknown)}. Available: {', '.join(columns)}")
    print(f"manifest: {args.manifest} ({len(rows)} rows; columns: {', '.join(columns)})")

    key_cols = [args.split] + ([args.label] if args.label else [])
    broken = [i + 2 for i, r in enumerate(rows) if not all(r[c] for c in key_cols)]
    if broken:
        report("FAIL", f"{len(broken)} rows have an empty {' or '.join(key_cols)} (manifest rows {rows_hint(broken)}); they are excluded")
        rows = [r for r in rows if all(r[c] for c in key_cols)]
    order = split_order(rows, args.split, args.order)
    check_splits(rows, args.label, args.split, order)

    if args.group:
        print("\n== Group leakage")
        for col in args.group:
            check_leakage(rows, [r[col] for r in rows], args.split, col)
    if args.path:
        print(f"\n== Duplicate file contents ({args.path})")
        root = args.root or os.path.dirname(os.path.abspath(args.manifest))
        check_duplicates(rows, file_keys(rows, args.path, root), args.label, args.split, "identical files")
    for col in args.text:
        print(f"\n== Duplicate text ({col}, case and whitespace ignored)")
        check_duplicates(rows, text_keys(rows, col), args.label, args.split, f"identical {col}")
    for col in args.near:
        print(f"\n== Near duplicates across splits ({col}, IDF-weighted token overlap)")
        check_near(rows, col, args.split, args.label, args.near_threshold, args.near_max_df)
    if args.time:
        check_time(rows, args.split, args.time, order)
    if args.attr:
        check_shortcuts(rows, args.label, args.split, args.attr, order, args.shortcut_threshold)

    print(f"\n== Summary: {counts['FAIL']} FAIL, {counts['WARN']} WARN")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
