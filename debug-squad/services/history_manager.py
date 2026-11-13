"""Review history persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config.settings import Settings, get_settings
from models.schemas import ReviewSession

MIN_CREDENTIAL_LENGTH = 12


@dataclass
class HistoryEntry:
    """Lightweight summary for the history list, without loading everything."""

    session_id: str
    created_at: str
    filename: str
    intent: str
    verification: str
    issues: int
    attempts: int
    path: Path

    @property
    def created_display(self) -> str:
        try:
            return datetime.fromisoformat(self.created_at).strftime("%d %b %Y, %H:%M")
        except ValueError:
            return self.created_at


class HistoryManager:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.directory = self.settings.history_path


    def _path_for(self, session_id: str) -> Path:
        return self.directory / f"review-{session_id}.json"

    def save(self, session: ReviewSession) -> Path:
        """Persist a session."""
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = session.model_dump(mode="json")
        blob = json.dumps(payload, indent=2, default=str)

        key = self.settings.groq_api_key
        if key and len(key) >= MIN_CREDENTIAL_LENGTH and key in blob:
            raise ValueError(
                "Refusing to write history: the session contains the API key."
            )

        path = self._path_for(session.session_id)
        path.write_text(blob, encoding="utf-8")
        return path

    def list_entries(self, limit: int = 50) -> list[HistoryEntry]:
        """Newest first."""
        if not self.directory.exists():
            return []

        entries: list[HistoryEntry] = []
        for path in sorted(
            self.directory.glob("review-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            intent = (data.get("intent") or {}).get("intent", "unknown")
            issues = len((data.get("bug_report") or {}).get("issues", []))
            issues += len((data.get("style_report") or {}).get("ruff_issues", []))
            entries.append(
                HistoryEntry(
                    session_id=data.get("session_id", path.stem),
                    created_at=data.get("created_at", ""),
                    filename=data.get("filename", "submitted.py"),
                    intent=intent,
                    verification=data.get("verification", "Not Yet Verified"),
                    issues=issues,
                    attempts=len(data.get("attempts", [])),
                    path=path,
                )
            )
        return entries

    def load(self, session_id: str) -> ReviewSession | None:
        path = self._path_for(session_id)
        if not path.exists():
            return None
        try:
            return ReviewSession.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception:  # noqa: BLE001
            return None

    def delete(self, session_id: str) -> bool:
        path = self._path_for(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def count(self) -> int:
        if not self.directory.exists():
            return 0
        return len(list(self.directory.glob("review-*.json")))
