# -*- coding: utf-8 -*-
"""관리 작업(버튼 실행) 레지스트리 + 실행 엔진.

비개발자가 /manage/tasks 페이지에서 버튼으로 정비 스크립트를 실행한다.
  - 작업 정의는 TASKS 리스트에 dict 로 추가하면 카드가 자동 생성된다.
  - 실행 모델: 2단계 — 'preview'(dry-run/scan) 로 영향 확인 후 'apply' 로 실제 실행.
  - 동시 실행은 1개로 제한(모두 DB/바이마 외부 호출이라 직렬화).
  - 작업 상태/로그는 task_runs/ 아래 파일로 저장 → gunicorn 멀티 워커에서도 조회 가능.

주의:
  - 스크립트는 manage_server 가 떠 있는 머신(AWS)에서 subprocess 로 실행된다.
  - 'needs_warp' 로 표시된 작업은 Cloudflare WARP + 브라우저/쿠키가 필요해
    AWS 환경에서 실패할 수 있다(카드에 경고 배지로 표시).
"""

import os
import sys
import json
import uuid
import shlex
import signal
import threading
import subprocess
from pathlib import Path
from datetime import datetime

_BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _BASE_DIR.parent
RUN_DIR = _BASE_DIR / "task_runs"
RUN_DIR.mkdir(exist_ok=True)

# 작업 1건당 옵션 값 최대 길이 (방어용)
_MAX_OPT_LEN = 100


# ---------------------------------------------------------------------------
# 작업 레지스트리 — 카드를 추가하려면 여기에 dict 를 append 하면 된다.
#
# 필드:
#   id            : URL/상태파일에 쓰이는 안정적 slug
#   title         : 비개발자용 제목
#   description   : 무슨 작업인지 설명 (영향 포함)
#   script        : PROJECT_ROOT 기준 상대 경로
#   env           : 'aws_ok'(서버에서 바로 실행 가능) | 'needs_warp'(WARP/브라우저 필요)
#   destructive   : 바이마/DB 를 변경하는가 (confirm 강제)
#   options       : 인라인 옵션 컨트롤 목록
#                     {flag, type:'bool'|'number'|'text'|'choice', label, ...}
#   preview_flags : '영향 미리보기' 모드에서 추가할 인자 (dry-run/scan/count)
#   preview_label : 미리보기 버튼 라벨 (기본 '영향 미리보기')
#   apply_flags   : '실행' 모드에서 추가할 인자
# ---------------------------------------------------------------------------
TASKS = [
    {
        "id": "orphan_delete",
        "title": "바이마엔 있는데 DB엔 없는 상품 정리 (고아 상품 삭제)",
        "description": (
            "바이마 출품 리스트를 크롤링해, 우리 DB에 행이 아예 없는 '고아' 상품을 "
            "찾아 바이마에서 삭제합니다. '크롤링 다시 하기'를 켜면 새로 스캔하고, 끄면 "
            "직전 스캔 파일을 재사용합니다. (DB는 변경하지 않음)"
        ),
        "script": "buyma_cleaners/buyma_orphan_cleaner.py",
        "env": "needs_warp",
        "destructive": True,
        "options": [
            {
                "flag": "--scan",
                "type": "bool",
                "label": "크롤링 다시 하기 (스캔 파일 새로 생성 · 수 분 소요). 끄면 기존 스캔 파일 재사용",
                "default": True,
            },
        ],
        "preview_flags": ["--delete", "--dry-run"],
        "preview_label": "영향 미리보기 (삭제 대상)",
        "apply_flags": ["--delete"],
    },
    {
        "id": "ghost_clean",
        "title": "DB엔 등록인데 바이마엔 없는 데이터 정리 (유령 상품)",
        "description": (
            "DB에는 출품중(is_published=1)으로 돼 있지만 바이마 크롤링 결과 실제로는 "
            "없는 '유령' 상품을 찾아, DB를 미등록(is_published=0)으로 정리합니다. "
            "'크롤링 다시 하기'를 켜면 새로 스캔하고, 끄면 직전 스캔 파일을 재사용합니다."
        ),
        "script": "buyma_cleaners/buyma_orphan_cleaner.py",
        "env": "needs_warp",
        "destructive": True,
        "options": [
            {
                "flag": "--scan",
                "type": "bool",
                "label": "크롤링 다시 하기 (스캔 파일 새로 생성 · 수 분 소요). 끄면 기존 스캔 파일 재사용",
                "default": True,
            },
        ],
        "preview_flags": ["--clean-ghost", "--dry-run"],
        "preview_label": "영향 미리보기 (정리 대상)",
        "apply_flags": ["--clean-ghost"],
    },
]

_TASKS_BY_ID = {t["id"]: t for t in TASKS}


def list_tasks():
    """프론트로 내보낼 작업 메타(스크립트 경로는 노출 안 함)."""
    out = []
    for t in TASKS:
        out.append({
            "id": t["id"],
            "title": t["title"],
            "description": t["description"],
            "env": t.get("env", "aws_ok"),
            "destructive": t.get("destructive", True),
            "options": t.get("options", []),
            "preview_label": t.get("preview_label", "영향 미리보기"),
        })
    return out


# ---------------------------------------------------------------------------
# 실행 엔진
# ---------------------------------------------------------------------------
def _run_path(job_id):
    return RUN_DIR / f"{job_id}.json"


def _log_path(job_id):
    return RUN_DIR / f"{job_id}.log"


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def _read_meta(job_id):
    p = _run_path(job_id)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_meta(job_id, meta):
    tmp = _run_path(job_id).with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    tmp.replace(_run_path(job_id))


def _any_job_running():
    """실행 중(프로세스 살아있음)인 작업이 하나라도 있으면 그 meta 반환."""
    for p in RUN_DIR.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            continue
        if m.get("status") == "running" and _pid_alive(m.get("pid")):
            return m
    return None


class ValidationError(Exception):
    pass


def _build_option_args(task, options):
    """프론트에서 받은 옵션을 검증해 argv 조각으로 변환."""
    args = []
    specs = {o["flag"]: o for o in task.get("options", [])}
    options = options or {}
    for flag, raw in options.items():
        spec = specs.get(flag)
        if spec is None:
            raise ValidationError(f"허용되지 않은 옵션: {flag}")
        otype = spec.get("type", "text")
        if otype == "bool":
            if raw in (True, "true", "1", 1, "on"):
                args.append(flag)
            continue
        # 값 옵션: 비어있으면 생략
        if raw is None:
            continue
        val = str(raw).strip()
        if val == "":
            continue
        if len(val) > _MAX_OPT_LEN:
            raise ValidationError(f"{flag} 값이 너무 깁니다.")
        if otype == "number":
            if not val.lstrip("-").isdigit():
                raise ValidationError(f"{flag} 는 숫자여야 합니다.")
            args.extend([flag, val])
        elif otype == "choice":
            choices = spec.get("choices", [])
            if val not in choices:
                raise ValidationError(f"{flag} 는 {choices} 중 하나여야 합니다.")
            args.extend([flag, val])
        else:  # text — argv 리스트 전달이라 셸 인젝션 없음
            args.extend([flag, val])
    return args


def start_run(task_id, mode, options):
    """작업 실행 시작. job_id 반환. mode: 'preview' | 'apply'."""
    task = _TASKS_BY_ID.get(task_id)
    if task is None:
        raise ValidationError(f"존재하지 않는 작업: {task_id}")
    if mode not in ("preview", "apply"):
        raise ValidationError("mode 는 preview 또는 apply 여야 합니다.")

    running = _any_job_running()
    if running is not None:
        raise RuntimeError(
            f"이미 실행 중인 작업이 있습니다: {running.get('task_id')} "
            f"(job {running.get('job_id')}). 끝난 뒤 다시 시도하세요."
        )

    script_abs = (PROJECT_ROOT / task["script"]).resolve()
    if not script_abs.exists():
        raise ValidationError(f"스크립트를 찾을 수 없습니다: {task['script']}")

    mode_flags = task.get("preview_flags" if mode == "preview" else "apply_flags", [])
    opt_args = _build_option_args(task, options)
    argv = [sys.executable, "-u", str(script_abs)] + list(mode_flags) + opt_args

    job_id = uuid.uuid4().hex[:12]
    started_at = datetime.now().isoformat(timespec="seconds")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    log_file = _log_path(job_id)
    with open(log_file, "w", encoding="utf-8") as lf:
        lf.write(f"$ {' '.join(shlex.quote(a) for a in argv)}\n")
        lf.write(f"# 시작: {started_at}\n")
        lf.write("-" * 60 + "\n")
        lf.flush()
        logf = open(log_file, "a", encoding="utf-8")
        proc = subprocess.Popen(
            argv,
            cwd=str(PROJECT_ROOT),
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,  # 자체 프로세스 그룹 → 중지 시 그룹 단위로 안전하게 종료
        )

    meta = {
        "job_id": job_id,
        "task_id": task_id,
        "task_title": task["title"],
        "mode": mode,
        "status": "running",
        "pid": proc.pid,
        "returncode": None,
        "started_at": started_at,
        "ended_at": None,
    }
    _write_meta(job_id, meta)

    def _monitor(p, jid, lf_handle):
        rc = p.wait()
        lf_handle.close()
        m = _read_meta(jid) or meta
        if rc == 0:
            m["status"] = "done"
        else:
            m["status"] = "stopped" if m.get("stop_requested") else "error"
        m["returncode"] = rc
        m["ended_at"] = datetime.now().isoformat(timespec="seconds")
        _write_meta(jid, m)

    threading.Thread(target=_monitor, args=(proc, job_id, logf), daemon=True).start()
    return meta


def get_run(job_id):
    meta = _read_meta(job_id)
    if meta is None:
        return None
    # 모니터 스레드가 죽었는데 프로세스도 끝난 경우 보정
    if meta.get("status") == "running" and not _pid_alive(meta.get("pid")):
        meta["status"] = "stopped" if meta.get("stop_requested") else "unknown"
        meta["ended_at"] = meta.get("ended_at") or datetime.now().isoformat(timespec="seconds")
        _write_meta(job_id, meta)
    return meta


def stop_run(job_id):
    """실행 중인 작업 프로세스를 중지(SIGTERM, 프로세스 그룹)."""
    meta = _read_meta(job_id)
    if meta is None:
        raise ValidationError("작업을 찾을 수 없습니다.")
    pid = meta.get("pid")
    if meta.get("status") != "running" or not _pid_alive(pid):
        cur = get_run(job_id) or {}
        return {"stopped": False, "status": cur.get("status", "unknown")}
    # 중지 요청 표시 → 모니터/보정이 'stopped'로 기록
    meta["stop_requested"] = True
    _write_meta(job_id, meta)
    try:
        if hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError) as e:
        raise RuntimeError(f"중지 실패: {e}")
    return {"stopped": True}


def read_log(job_id, offset=0):
    p = _log_path(job_id)
    if not p.exists():
        return {"text": "", "size": 0}
    size = p.stat().st_size
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    if offset > size:
        offset = 0
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        text = f.read()
    return {"text": text, "size": size}


def list_recent_runs(limit=20):
    metas = []
    for p in RUN_DIR.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                metas.append(json.load(f))
        except Exception:
            continue
    metas.sort(key=lambda m: m.get("started_at") or "", reverse=True)
    return metas[:limit]


def _running_job_ids():
    """현재 프로세스가 살아있는 실행 중 작업의 job_id 집합."""
    ids = set()
    for p in RUN_DIR.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            continue
        if m.get("status") == "running" and _pid_alive(m.get("pid")):
            jid = m.get("job_id")
            if jid:
                ids.add(jid)
    return ids


# orphan_cleaner 가 PROJECT_ROOT(cwd) 에 생성하는 산출물 파일들
_ORPHAN_ARTIFACT_FILES = (
    "buyma_all_product_ids.json",
    "buyma_orphan_ids.json",
    "buyma_orphan_products.csv",
    "buyma_ghost_ids.json",
    "buyma_ghost_products.csv",
    "buyma_crawl_progress.json",
)
_CRAWL_PROGRESS_FILE = "buyma_crawl_progress.json"
_COOKIE_FILE = "buyma_cookies.json"


def cleanup_artifacts(include_cookie=True):
    """orphan_cleaner 가 생성한 목록 파일(+선택적으로 로그인 쿠키)을 삭제.

    실행 중인 작업이 있으면 산출물을 읽고 있을 수 있으므로 거부한다.
    반환: {deleted, freed_bytes, files}
    """
    if _any_job_running() is not None:
        raise RuntimeError("실행 중인 작업이 있어 정리할 수 없습니다. 끝난 뒤 다시 시도하세요.")
    targets = list(_ORPHAN_ARTIFACT_FILES)
    if include_cookie:
        targets.append(_COOKIE_FILE)
    deleted = 0
    freed = 0
    removed = []
    for name in targets:
        p = PROJECT_ROOT / name
        if not p.exists() or not p.is_file():
            continue
        try:
            sz = p.stat().st_size
            p.unlink()
            deleted += 1
            freed += sz
            removed.append(name)
        except OSError:
            continue
    return {"deleted": deleted, "freed_bytes": freed, "files": removed}


# PC에서 받아 실행하는 로그인 도구 (사람이 직접 로그인 → 쿠키 생성)
LOGIN_TOOL_REL = "buyma_cleaners/buyma_login.py"


def login_tool_path():
    return PROJECT_ROOT / LOGIN_TOOL_REL


def cookie_path():
    return PROJECT_ROOT / _COOKIE_FILE


def scan_status():
    """크롤링 스캔 산출물(json·csv) 존재 여부 + 생성 일시 + 미완료(이어하기) 여부."""
    files = []
    latest = None
    for name in _ORPHAN_ARTIFACT_FILES:
        p = PROJECT_ROOT / name
        if p.exists() and p.is_file():
            st = p.stat()
            mt = datetime.fromtimestamp(st.st_mtime).isoformat(sep=" ", timespec="seconds")
            files.append({"name": name, "size": st.st_size, "mtime": mt})
            if latest is None or mt > latest:
                latest = mt
    incomplete = (PROJECT_ROOT / _CRAWL_PROGRESS_FILE).exists()
    return {"exists": bool(files), "files": files, "latest": latest, "incomplete": incomplete}


def cookie_status():
    """서버에 업로드된 로그인 쿠키 상태."""
    p = cookie_path()
    if p.exists() and p.is_file():
        return {"exists": True, "size": p.stat().st_size}
    return {"exists": False, "size": 0}


def save_cookie(raw_bytes):
    """업로드된 쿠키 파일(JSON 리스트)을 검증 후 서버에 저장."""
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        raise ValidationError("쿠키 파일이 올바른 JSON 이 아닙니다.")
    if (not isinstance(data, list) or not data
            or not all(isinstance(x, dict) and "name" in x for x in data)):
        raise ValidationError(
            "쿠키 형식이 올바르지 않습니다. 로그인 도구가 만든 buyma_cookies.json 을 올려주세요."
        )
    with open(cookie_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"saved": True, "count": len(data)}


def delete_cookie():
    """서버의 로그인 쿠키만 삭제 (보안)."""
    p = cookie_path()
    if not p.exists():
        return {"deleted": False, "freed_bytes": 0}
    sz = p.stat().st_size
    try:
        p.unlink()
    except OSError as e:
        raise RuntimeError(f"쿠키 삭제 실패: {e}")
    return {"deleted": True, "freed_bytes": sz}


def cleanup_runs():
    """끝난 작업의 로그/상태 파일을 삭제해 서버 용량을 회수.

    실행 중(프로세스 살아있음)인 작업의 파일은 보존한다.
    반환: {deleted, freed_bytes, kept_running}
    """
    keep = _running_job_ids()
    deleted = 0
    freed = 0
    for p in list(RUN_DIR.iterdir()):
        if not p.is_file():
            continue
        # job_id = 확장자 앞부분 (xxx.json / xxx.log / xxx.json.tmp)
        stem = p.name.split(".")[0]
        if stem in keep:
            continue
        try:
            sz = p.stat().st_size
            p.unlink()
            deleted += 1
            freed += sz
        except OSError:
            continue
    return {"deleted": deleted, "freed_bytes": freed, "kept_running": len(keep)}
