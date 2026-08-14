# -*- coding: utf-8 -*-
"""
draft 점수 일배치: 셀 집계 → 채점.

  python scoring/run_daily_draft.py              # dry-run (쓰기 없음)
  python scoring/run_daily_draft.py --execute    # 실제 갱신

로그: logs/draft_scoring_YYYYMMDD.log
소요: 셀 ~10–20분 + 채점 ~1분
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
from datetime import datetime

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

SCORING_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCORING_DIR)
LOG_DIR = os.path.join(ROOT, "logs")


def _log(fp, msg: str):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    if fp:
        fp.write(line + "\n")
        fp.flush()


def run_step(name: str, script: str, execute: bool, fp) -> int:
    cmd = [sys.executable, "-u", os.path.join(SCORING_DIR, script)]
    if execute:
        cmd.append("--execute")
    _log(fp, f"[start] {name}: {' '.join(cmd)}")
    t0 = datetime.now()
    p = subprocess.run(
        cmd,
        cwd=SCORING_DIR,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
    )
    elapsed = (datetime.now() - t0).total_seconds()
    _log(fp, f"[end] {name} rc={p.returncode} in {elapsed:.1f}s")
    return p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument(
        "--skip-cells",
        action="store_true",
        help="셀 집계 건너뛰고 채점만 (비상용)",
    )
    ap.add_argument(
        "--skip-score",
        action="store_true",
        help="셀만 돌리고 채점 생략",
    )
    ap.add_argument(
        "--skip-tokens",
        action="store_true",
        help="품번 증분 생략",
    )
    args = ap.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(
        LOG_DIR, f"draft_scoring_{datetime.now().strftime('%Y%m%d')}.log"
    )
    mode = "EXECUTE" if args.execute else "DRY-RUN"

    with open(log_path, "a", encoding="utf-8") as fp:
        _log(fp, f"===== run_daily_draft {mode} =====")
        _log(fp, f"log={log_path}")

        if not args.skip_tokens:
            rc = run_step(
                "incremental_mn_tokens",
                "incremental_mn_tokens.py",
                args.execute,
                fp,
            )
            if rc != 0:
                _log(fp, f"ABORT after tokens rc={rc}")
                sys.exit(rc)
        else:
            _log(fp, "[skip] tokens")

        if not args.skip_cells:
            rc = run_step("build_market_cells", "build_market_cells.py", args.execute, fp)
            if rc != 0:
                _log(fp, f"ABORT after cells rc={rc}")
                sys.exit(rc)
        else:
            _log(fp, "[skip] cells")

        if not args.skip_score:
            rc = run_step("score_draft", "score_draft.py", args.execute, fp)
            if rc != 0:
                _log(fp, f"ABORT after score rc={rc}")
                sys.exit(rc)
        else:
            _log(fp, "[skip] score")

        _log(fp, "===== DONE OK =====")
    sys.exit(0)


if __name__ == "__main__":
    main()
