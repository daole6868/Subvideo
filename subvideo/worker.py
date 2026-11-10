"""Background worker process.

Protocol: JSON lines.
  stdin  <- {"cmd": "run", "id": "...", "stage": "...", "project": "<dir>", "params": {...}}
            {"cmd": "cancel", "id": "..."}   {"cmd": "quit"}
  stdout -> {"type": "ready"} | {"id", "type": "progress", "value", "message"}
            {"id", "type": "log", "level", "message"} | {"id", "type": "done", "result"}
            {"id", "type": "error", "error", "trace"} | {"id", "type": "cancelled"}
Anything else that libraries print goes to stderr (shown in the log panel).
Models stay loaded between jobs; kill the process to free GPU memory.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback


def main() -> None:
    proto = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8", errors="replace")
    os.dup2(2, 1)  # C-level prints -> stderr
    sys.stdout = sys.stderr
    lock = threading.Lock()

    def send(obj: dict) -> None:
        with lock:
            proto.write(json.dumps(obj, ensure_ascii=False) + "\n")
            proto.flush()

    from .config import Settings

    Settings.get().apply_env()
    from .core.jobs import Cancelled, JobContext
    from .core.project import Project
    from .pipeline.stages import run_stage

    jobs: "queue.Queue[dict | None]" = queue.Queue()
    cancelled: set[str] = set()

    def runner() -> None:
        while True:
            msg = jobs.get()
            if msg is None:
                return
            jid = msg.get("id", "")
            if jid in cancelled:
                send({"id": jid, "type": "cancelled"})
                continue
            ctx = JobContext(
                on_progress=lambda v, m, jid=jid: send({"id": jid, "type": "progress", "value": v, "message": m}),
                on_log=lambda lv, m, jid=jid: send({"id": jid, "type": "log", "level": lv, "message": m}),
                is_cancelled=lambda jid=jid: jid in cancelled,
            )
            project = None
            try:
                Settings._instance = None  # pick up settings changed in the UI
                Settings.get().apply_env()
                project = Project.load(msg["project"]) if msg.get("project") else Project(os.getcwd())
                result = run_stage(msg["stage"], project, msg.get("params") or {}, ctx)
                if msg.get("project"):
                    project.save()
                send({"id": jid, "type": "done", "result": result})
            except Cancelled:
                if project is not None and msg.get("project") and msg.get("save_on_cancel", True):
                    try:
                        project.save()
                    except Exception:
                        pass
                send({"id": jid, "type": "cancelled"})
            except BaseException as e:  # noqa: BLE001 - report everything to the UI
                tb = traceback.format_exc()
                sys.stderr.write(tb)
                if project is not None and msg.get("project"):
                    try:
                        project.save()
                    except Exception:
                        pass
                send({"id": jid, "type": "error", "error": f"{type(e).__name__}: {e}", "trace": tb})

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    send({"type": "ready", "pid": os.getpid(), "python": sys.executable})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        cmd = msg.get("cmd")
        if cmd == "run":
            jobs.put(msg)
        elif cmd == "cancel":
            cancelled.add(msg.get("id", ""))
        elif cmd == "quit":
            break
    jobs.put(None)


if __name__ == "__main__":
    main()
