"""테스트는 SQLite 백엔드 강제 (운영 기본은 MySQL)."""
import os

os.environ.setdefault("MONITOR_DB", "sqlite")
