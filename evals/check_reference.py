"""Week 3: run every golden question's reference SQL and check it still works.

    python evals/check_reference.py            # check, compare with the last saved run
    python evals/check_reference.py --save     # also save this run's answers as evidence

Connects as app_reader (APP_DATABASE_URL), inside a READ ONLY transaction, so a
reference query can never change data even if someone writes a bad one.

For each question it checks the query runs and returns at least one row. With
--save it writes the answers to evals/reference_runs/<time>.json; each run is
compared with the newest saved one, so you can see which answers moved as the
sync brought in new data. Reference answers are SQL, not fixed values: the
correct answer is whatever the query returns at evaluation time.
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "golden_questions.csv"
RUNS_DIR = HERE / "reference_runs"
REQUIRED = {"total": 15, "baseline": 8, "should_decline": 3}


def as_text(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true", help="save answers to evals/reference_runs/")
    args = parser.parse_args()

    load_dotenv()
    url = os.environ.get("APP_DATABASE_URL")
    if not url:
        sys.exit("[FAIL] APP_DATABASE_URL is not set. The checker must run as app_reader.")

    with QUESTIONS.open(encoding="utf-8", newline="") as f:
        questions = list(csv.DictReader(f))

    previous_file = max(RUNS_DIR.glob("*.json"), default=None) if RUNS_DIR.exists() else None
    previous = json.loads(previous_file.read_text(encoding="utf-8"))["answers"] if previous_file else {}

    answers, failures, changed = {}, [], []
    with psycopg.connect(url, connect_timeout=10, autocommit=True) as conn:
        user = conn.execute("select current_user").fetchone()[0]
        print(f"Connected as {user}" + ("" if user == "app_reader" else "  [warn] expected app_reader"))
        for q in questions:
            if q["category"] == "should_decline":
                print(f"  {q['id']}  decline   (no reference SQL by design)")
                continue
            try:
                with conn.transaction():
                    conn.execute("set transaction read only")
                    conn.execute("set local statement_timeout = '20s'")
                    rows = conn.execute(q["reference_sql"]).fetchall()
            except psycopg.Error as error:
                failures.append(q["id"])
                print(f"  {q['id']}  FAIL      {str(error).strip().splitlines()[0]}")
                continue
            answer = [[as_text(v) for v in row] for row in rows[:10]]
            answers[q["id"]] = answer
            empty = not rows or all(v is None for v in rows[0])
            if empty:
                failures.append(q["id"])
            moved = q["id"] in previous and previous[q["id"]] != answer
            if moved:
                changed.append(q["id"])
            note = "EMPTY" if empty else ("changed" if moved else ("same" if q["id"] in previous else "new"))
            first = ", ".join(answer[0]) if answer else ""
            print(f"  {q['id']}  {note:8}  {len(rows):3} row(s)  first: {first[:70]}")

    counts = Counter(q["category"] for q in questions)
    baseline = sum(q["source"] == "baseline" for q in questions)
    print(f"\n{len(questions)} questions ({dict(counts)}); {baseline} from the baseline log")
    for name, have in (("total", len(questions)), ("baseline", baseline), ("should_decline", counts["should_decline"])):
        if have < REQUIRED[name]:
            print(f"  [todo] M3 needs at least {REQUIRED[name]} {name.replace('_', '-')} questions; you have {have}")
    if previous_file:
        print(f"Compared with {previous_file.name}: {len(changed)} answer(s) changed {changed if changed else ''}")

    if args.save:
        RUNS_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        out = RUNS_DIR / f"{stamp}.json"
        out.write_text(json.dumps({"checked_at": stamp, "answers": answers}, indent=1), encoding="utf-8")
        print(f"Saved {out.relative_to(HERE.parent)}")

    if failures:
        print(f"[FAIL] {len(failures)} reference query/queries failed or returned nothing: {failures}")
        sys.exit(1)
    print("[ok] Every reference query ran and returned a result.")


if __name__ == "__main__":
    main()
