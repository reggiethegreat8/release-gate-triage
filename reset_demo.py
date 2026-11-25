"""Put everything the demo touches back to a clean state.

Run from the repo root with:  uv run python reset_demo.py

  - deletes the saved runs (checkpoints.db), the reviewers' saved preferences
    (memory.db) and the recorded actions (actions.db);
  - rebuilds the source files and the store from the case files, so the
    store is known to match them.

It touches nothing in LangSmith and makes no model call.
"""

from pathlib import Path

from store.api import check_store
from store.load_store import load_store
from store.make_sources import write_sources

LOCAL_STATE = ["checkpoints.db", "memory.db", "actions.db"]

if __name__ == "__main__":
    for name in LOCAL_STATE:
        for path in Path(".").glob(f"{name}*"):  # also the -wal and -shm files SQLite may leave
            path.unlink()
        # Said every time, whether or not the file was there: either way it is gone now.
        print(f"deleted {name}")
    write_sources()
    counts = load_store()
    check_store()
    print("rebuilt the store: " + ", ".join(f"{count} {name}" for name, count in counts.items()))
    print("ready for the demo")
