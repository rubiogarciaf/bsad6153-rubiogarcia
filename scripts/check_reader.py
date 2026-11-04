"""Week 2: prove the read-only role really is read-only.

    python scripts/check_reader.py

Connects as app_reader (APP_DATABASE_URL) and tries three things:
  1. a SELECT, which must succeed
  2. an INSERT, which must be refused
  3. a DELETE, which must be refused
The write attempts run inside a transaction that is always rolled back, so even
if the role were set up wrongly, this script would not change any data.
"""

import os
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("APP_DATABASE_URL")
if not url:
    sys.exit("[FAIL] APP_DATABASE_URL is not set. Add the app_reader connection string to .env.")

ATTEMPTS = [
    ("INSERT", "insert into sync_runs (status, trigger) values ('success', 'manual')"),
    ("DELETE", "delete from participants"),
]

ok = True
with psycopg.connect(url, connect_timeout=10) as conn:
    user = conn.execute("select current_user").fetchone()[0]
    print(f"Connected as: {user}")
    if user != "app_reader":
        print("[warn] Expected app_reader. Is APP_DATABASE_URL using the right user?")

    games = conn.execute("select count(*) from matches").fetchone()[0]
    print(f"[ok]   SELECT allowed: matches has {games} rows")
    conn.rollback()

    for label, statement in ATTEMPTS:
        try:
            with conn.transaction(force_rollback=True):
                conn.execute(statement)
            print(f"[FAIL] {label} was allowed. Check db/roles.sql grants. (Rolled back; no data changed.)")
            ok = False
        except psycopg.errors.InsufficientPrivilege as error:
            print(f"[ok]   {label} denied: {str(error).strip().splitlines()[0]}")

sys.exit(0 if ok else 1)
