"""Memory Manager — pipeline state persistence and session management (Phase 4).

Saves/loads complete pipeline execution state to disk for:
  - Crash recovery: resume a failed pipeline
  - Audit trail: full execution history for debugging
  - Session sharing: export state for analysis

Storage format: JSON files in pipeline_memory/ directory.

Anti-bloat: enforces 4KB index limit, archives old sessions.
"""

import json as json_mod
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import PipelineState, ContextPackage


# ── Memory constants ──

INDEX_MAX_BYTES = 4096   # 4KB — refuse to start if exceeded
INDEX_WARN_BYTES = 3072  # 3KB — warn and suggest archiving
DEFAULT_MEMORY_DIR = "pipeline_memory"


class MemoryManager:
    """Manages pipeline state persistence with anti-bloat enforcement.

    Directory structure:
      pipeline_memory/
        _index.json          # Lightweight session index (<4KB)
        sessions/
          {project}_{ts}.json  # Full session state
        archive/
          {project}_{ts}.json  # Archived old sessions
    """

    def __init__(self, memory_dir=DEFAULT_MEMORY_DIR):
        # type: (str) -> None
        self.memory_dir = Path(memory_dir)
        self.index_path = self.memory_dir / "_index.json"
        self.sessions_dir = self.memory_dir / "sessions"
        self.archive_dir = self.memory_dir / "archive"

        for d in [self.sessions_dir, self.archive_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # ── Health check ──

    def health_check(self):
        # type: () -> dict
        """Check memory health. Returns status dict."""
        result = {
            "healthy": True,
            "index_bytes": 0,
            "session_count": 0,
            "warnings": [],
            "errors": [],
        }

        if self.index_path.exists():
            result["index_bytes"] = self.index_path.stat().st_size
            if result["index_bytes"] > INDEX_MAX_BYTES:
                result["healthy"] = False
                result["errors"].append(
                    "Index exceeds {}B limit ({}B). Archive old sessions.".format(
                        INDEX_MAX_BYTES, result["index_bytes"]))
            elif result["index_bytes"] > INDEX_WARN_BYTES:
                result["warnings"].append(
                    "Index approaching limit ({}B/{}B). Consider archiving.".format(
                        result["index_bytes"], INDEX_WARN_BYTES))

        sessions = list(self.sessions_dir.glob("*.json"))
        result["session_count"] = len(sessions)
        return result

    # ── Session save/load ──

    def save_session(self, state, gate_history=None):
        # type: (PipelineState, list) -> str
        """Save pipeline state to a session file. Returns session ID."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        session_id = "{}_{}".format(state.project_name.replace(" ", "_"), ts)

        data = self._serialize_state(state, gate_history)
        session_path = self.sessions_dir / "{}.json".format(session_id)
        session_path.write_text(
            json_mod.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        self._update_index(session_id, data)
        return session_id

    def load_session(self, session_id):
        # type: (str) -> dict
        """Load a session from disk. Returns raw session data dict."""
        session_path = self.sessions_dir / "{}.json".format(session_id)
        if not session_path.exists():
            raise FileNotFoundError(
                "Session '{}' not found in {}".format(session_id, self.sessions_dir))

        return json_mod.loads(session_path.read_text(encoding="utf-8"))

    def load_latest(self, project_name=None):
        # type: (str) -> dict
        """Load the most recent session, optionally filtered by project."""
        index = self._read_index()
        entries = index.get("sessions", [])

        if project_name:
            entries = [e for e in entries if project_name in e.get("session_id", "")]

        if not entries:
            raise FileNotFoundError(
                "No sessions found{}".format(
                    " for project '{}'".format(project_name) if project_name else ""))

        # Entries are already sorted by timestamp (newest first)
        return self.load_session(entries[0]["session_id"])

    def list_sessions(self, project_name=None):
        # type: (str) -> list
        """List all saved sessions, newest first."""
        index = self._read_index()
        entries = index.get("sessions", [])
        if project_name:
            entries = [e for e in entries if project_name in e.get("session_id", "")]
        return entries

    # ── Archiving ──

    def archive_old_sessions(self, keep_latest=5):
        # type: (int) -> int
        """Archive sessions beyond the latest N. Returns number archived."""
        index = self._read_index()
        entries = index.get("sessions", [])

        if len(entries) <= keep_latest:
            return 0

        archived = 0
        for entry in entries[keep_latest:]:
            sid = entry["session_id"]
            src = self.sessions_dir / "{}.json".format(sid)
            dst = self.archive_dir / "{}.json".format(sid)
            if src.exists():
                src.rename(dst)
                archived += 1

        # Rebuild index with remaining sessions
        remaining = entries[:keep_latest]
        self._write_index({"sessions": remaining, "updated": time.time()})
        return archived

    # ── Internal ──

    def _serialize_state(self, state, gate_history=None):
        # type: (PipelineState, list) -> dict
        """Convert PipelineState to JSON-serializable dict."""
        return {
            "project_name": state.project_name,
            "loopback_count": state.loopback_count,
            "current_role": state.current_role,
            "max_iterations": state.max_iterations,
            "steps": [
                {
                    "package_id": pkg.package_id,
                    "source_role": pkg.source_role,
                    "agent_output": {
                        "role": pkg.agent_output.role,
                        "role_name": pkg.agent_output.role_name,
                        "part_a_files": pkg.agent_output.part_a_files,
                        "quality_gate": pkg.agent_output.quality_gate.value,
                        "contract_summary": pkg.agent_output.contract.summary if pkg.agent_output.contract else "",
                    },
                    "gate_status": pkg.gate_result.status.value if pkg.gate_result else "UNKNOWN",
                    "gate_fail_reasons": pkg.gate_result.fail_reasons if pkg.gate_result else [],
                    "created_at": pkg.created_at,
                }
                for pkg in state.steps
            ],
            "gate_history": [
                {
                    "status": g.status.value,
                    "fail_reasons": g.fail_reasons,
                    "loopback_target": g.loopback_target.value,
                    "verified_at": g.verified_at,
                }
                for g in (gate_history or [])
            ],
        }

    def _read_index(self):
        # type: () -> dict
        if self.index_path.exists():
            try:
                return json_mod.loads(self.index_path.read_text(encoding="utf-8"))
            except (json_mod.JSONDecodeError, ValueError):
                pass
        return {"sessions": [], "updated": 0}

    def _write_index(self, data):
        # type: (dict) -> None
        self.index_path.write_text(
            json_mod.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _update_index(self, session_id, session_data):
        # type: (str, dict) -> None
        index = self._read_index()
        entries = index.get("sessions", [])

        # Remove existing entry with same ID if present
        entries = [e for e in entries if e.get("session_id") != session_id]

        # Insert at beginning (newest first)
        entries.insert(0, {
            "session_id": session_id,
            "project": session_data.get("project_name", ""),
            "steps": len(session_data.get("steps", [])),
            "loopbacks": session_data.get("loopback_count", 0),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        })

        # Keep only latest 20 entries in index to prevent bloat
        entries = entries[:20]

        self._write_index({
            "sessions": entries,
            "updated": time.time(),
        })


# ── Quick API ──

def save_pipeline(gate, memory_dir=DEFAULT_MEMORY_DIR):
    # type: (object, str) -> str
    """Save an AgentGate pipeline in one call. Returns session_id."""
    mm = MemoryManager(memory_dir)
    return mm.save_session(gate.state, gate._gate_history if hasattr(gate, '_gate_history') else None)


def load_pipeline(session_id=None, project_name=None, memory_dir=DEFAULT_MEMORY_DIR):
    # type: (str, str, str) -> dict
    """Load a saved pipeline session."""
    mm = MemoryManager(memory_dir)
    if session_id:
        return mm.load_session(session_id)
    return mm.load_latest(project_name)
