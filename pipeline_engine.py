# -*- coding: utf-8 -*-
"""
통합 파이프라인 엔진 (okmall orchestrator.py 의 resume+병렬 엔진을 몰/유닛 무관하게 일반화).

okmall(브랜드 단위)과 multisource 8몰(몰 단위)을 한 엔진으로:
  - resume: pipeline_batches/pipeline_control 기반, DONE stage 스킵 (#2)
  - 유닛 파이프라인 병렬 + stage 세마포어, phase 배리어 없음 (#3)
  - NEW(collect~register) / STOCK(refresh~reconcile) 트랙 동시 실행 (#4)

설계: reports/unified_pipeline_engine_design_20260618.md
전제 스키마: migrations/2026_pipeline_control_track.sql (track/unit_key 컬럼 + 유니크키)

이 모듈은 "엔진"만 제공. 유닛 목록·stage 구성·워커 명령은 run_daily_unified.py 가 주입.
"""

import os
import sys
import subprocess
import threading
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

import pymysql
from dotenv import load_dotenv

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'), override=True)

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
}

_log_lock = threading.Lock()


def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _log_lock:
        print(f"[{ts}] [{level}] {msg}", flush=True)


# =====================================================
# 유닛 / Stage 구성 타입 (run 파일이 주입)
# =====================================================
# Unit = {
#   'mall':          str,   # 'okmall' | 'kasina' | ... (pipeline_control.mall_name)
#   'unit_key':      str,   # 브랜드명(okmall) | '_ALL_'(multisource 몰 전체)
#   'track':         'NEW' | 'STOCK',
#   'brand_name':    str,   # 워커에 넘길 브랜드명 (multisource 는 unit_key 와 동일/미사용)
#   'site_resource': str,   # 사이트 접속 자원 키. 같은 키끼리 동시 접속 1개로 제한.
#                           #   naver 21몰 → 전부 'naver'(쿠키·캡챠 공유) / 독립몰 → 각자 mall 이름.
#                           #   생략 시 mall 로 기본.
#   'meta':          dict,  # worker_resolver 가 쓸 추가 정보(buyma_brand 등)
# }
#
# stage_plan = {'NEW': [stages...], 'STOCK': [stages...]}
# worker_resolver(unit, stage) -> List[List[str]]   # 실행할 subprocess 명령 리스트(순차). 빈 리스트면 no-op.
# stage_concurrency = {stage: int}                   # stage별 동시 실행 상한 (세마포어)
# site_access_stages = {stage, ...}                  # 사이트에 직접 접속하는 단계(collector·stock 등).
#                                                    #   이 단계는 unit['site_resource'] 자원 락(자원당 1개)을 추가로 획득.


class PipelineEngine:
    def __init__(self,
                 run_mode: str,
                 units: List[Dict],
                 stage_plan: Dict[str, List[str]],
                 worker_resolver: Callable[[Dict, str], List[List[str]]],
                 stage_concurrency: Optional[Dict[str, int]] = None,
                 site_access_stages: Optional[set] = None,
                 max_workers: Optional[int] = None,
                 dry_run: bool = False):
        self.run_mode = run_mode
        self.units = units
        self.stage_plan = stage_plan
        self.worker_resolver = worker_resolver
        self.dry_run = dry_run
        self.batch_id: Optional[str] = None
        self.db_lock = threading.Lock()

        # stage별 세마포어 (리소스 보호). 미지정 stage 는 무제한(아주 큰 값).
        sc = stage_concurrency or {}
        all_stages = {s for plan in stage_plan.values() for s in plan}
        self.stage_sems = {s: threading.Semaphore(sc.get(s, 9999)) for s in all_stages}

        # ── 사이트 접속 자원 잠금 (#4) ──
        #   collector·stock 처럼 몰 사이트에 직접 붙는 단계는, 같은 site_resource 끼리
        #   동시 접속 1개로 제한(naver 캡챠/차단 방지). track 이 달라도(collector=NEW, stock=STOCK)
        #   같은 자원이면 한 잠금을 공유 → "네이버 접근은 한 번에 하나만".
        self.site_access_stages = set(site_access_stages or ())
        # site_resource 가 지정된(공유 잠금 필요) 유닛만 자원 세마포어 생성.
        #   site_resource=None → 잠금 없음(독립 사이트, 예: 9몰 — 전부 병렬).
        #   캡챠로 직렬 접속이 필요한 몰만 같은 값(예: naver 21몰 전부 'naver')을 부여 → 그 값끼리 1개씩.
        site_resources = {u.get('site_resource') for u in units if u.get('site_resource')}
        self.site_sems = {r: threading.Semaphore(1) for r in site_resources}

        # 동시에 진행할 유닛 수 (트랙 무관 전체). 기본: CPU 보호 위해 작게.
        #   units 비어도 ThreadPoolExecutor(max_workers>=1) 보장(0이면 ValueError).
        self.max_workers = max_workers or max(1, min(len(units), 8))

    # ---- DB (★작업마다 새 연결 — 긴 작업(수십 분 subprocess) 중 유휴 타임아웃으로
    #         연결이 끊겨 상태기록이 실패하던 버그 방지. 연결을 들고 있지 않는다.) ----
    def _db(self):
        return pymysql.connect(**DB_CONFIG)

    def get_or_create_batch(self) -> str:
        """오늘 RUNNING 배치 있으면 이어받기(resume), 없으면 새로. 어제 RUNNING 은 FAILED 마감.
        (orchestrator.py 와 동일 — 단 run_mode 로만 구분하므로 통합 배치는 단일 run_mode 사용)."""
        with self.db_lock:
            conn = self._db()
            try:
                with conn.cursor() as cur:
                    today = date.today().strftime("%Y%m%d")
                    cur.execute(
                        """SELECT batch_id FROM pipeline_batches
                           WHERE status='RUNNING' AND run_mode=%s AND batch_id LIKE %s LIMIT 1""",
                        (self.run_mode, f"{today}%"))
                    row = cur.fetchone()
                    if row:
                        self.batch_id = row['batch_id']
                        log(f"오늘 기존 배치 이어받기: {self.batch_id}")
                    else:
                        cur.execute(
                            """UPDATE pipeline_batches SET status='FAILED', end_time=NOW()
                               WHERE status='RUNNING' AND run_mode=%s AND batch_id NOT LIKE %s""",
                            (self.run_mode, f"{today}%"))
                        if cur.rowcount:
                            log(f"어제 RUNNING 배치 {cur.rowcount}건 FAILED 마감")
                        self.batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                        cur.execute(
                            "INSERT INTO pipeline_batches (batch_id, run_mode, status) VALUES (%s,%s,'RUNNING')",
                            (self.batch_id, self.run_mode))
                        log(f"새 배치 생성: {self.batch_id} ({self.run_mode})")
                conn.commit()
            finally:
                conn.close()
        return self.batch_id

    def get_stage_status(self, unit: Dict, stage: str) -> str:
        conn = self._db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT status FROM pipeline_control
                       WHERE batch_id=%s AND mall_name=%s AND brand_name=%s AND track=%s AND stage=%s""",
                    (self.batch_id, unit['mall'], unit['unit_key'], unit['track'], stage))
                r = cur.fetchone()
                return r['status'] if r else 'PENDING'
        finally:
            conn.close()

    def set_stage_status(self, unit: Dict, stage: str, status: str, error_msg: Optional[str] = None):
        conn = self._db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO pipeline_control
                         (batch_id, mall_name, brand_name, unit_key, run_mode, track, stage, status, error_msg, started_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                       ON DUPLICATE KEY UPDATE status=VALUES(status), error_msg=VALUES(error_msg), updated_at=NOW()""",
                    (self.batch_id, unit['mall'], unit['unit_key'], unit['unit_key'],
                     self.run_mode, unit['track'], stage, status, error_msg))
            conn.commit()
        finally:
            conn.close()

    # ---- 실행 ----
    def run(self):
        try:
            self.get_or_create_batch()
            conn = self._db()
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE pipeline_batches SET total_brands=%s WHERE batch_id=%s",
                                (len(self.units), self.batch_id))
                conn.commit()
            finally:
                conn.close()

            log(f"유닛 {len(self.units)}개 (NEW {sum(u['track']=='NEW' for u in self.units)} / "
                f"STOCK {sum(u['track']=='STOCK' for u in self.units)}), 동시 {self.max_workers}")

            any_failed = False
            with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                futs = {ex.submit(self.run_unit_pipeline, u): u for u in self.units}
                for fut in as_completed(futs):
                    u = futs[fut]
                    tag = f"{u['mall']}/{u['unit_key']}/{u['track']}"
                    try:
                        ok = fut.result()
                        if not ok:
                            any_failed = True
                        log(f"{'✓' if ok else '✗'} [{tag}] 파이프라인 {'완료' if ok else '실패'}")
                    except Exception as e:
                        any_failed = True
                        log(f"✗ [{tag}] 예외: {e}", "ERROR")
            # ★ 실패 유닛이 있으면 배치를 COMPLETED 로 닫지 않고 RUNNING 유지.
            #   → 같은 날 재실행 시 이 배치를 이어받아(get_or_create_batch) 실패(ERROR)·미완 단계만 재시도.
            #     (성공 유닛의 단계는 DONE 이라 스킵.) 다음 날엔 어제 RUNNING 배치가 FAILED 로 마감됨.
            if any_failed:
                log("일부 유닛 실패 → 배치 RUNNING 유지 (재실행 시 실패/미완 단계만 이어서 재시도)", "WARNING")
            else:
                self.finish_batch()
        except Exception as e:
            log(f"엔진 치명적 오류: {e}", "ERROR")
            self.mark_batch_failed()

    def run_unit_pipeline(self, unit: Dict) -> bool:
        """한 유닛의 트랙 stage 체인을 순차 진행. DONE 은 스킵(resume).
        stage별 세마포어(리소스 보호) + 사이트 접속 단계는 site_resource 자원 락(자원당 1개).
        DONE 스킵은 사이트 접속이 없으므로 site 락을 잡지 않는다."""
        tag = f"{unit['mall']}/{unit['unit_key']}/{unit['track']}"
        site_key = unit.get('site_resource')   # None 이면 사이트 잠금 없음
        for stage in self.stage_plan[unit['track']]:
            with self.stage_sems[stage]:
                if self.get_stage_status(unit, stage) == 'DONE':
                    log(f"  [{tag}] [{stage}] 이미 완료 → 스킵")
                    continue
                cmds = self.worker_resolver(unit, stage)
                if not cmds:
                    self.set_stage_status(unit, stage, 'DONE', 'no-op')
                    continue
                # 사이트 접속 단계(collector·stock)면 site 자원 락 추가 획득
                #   → 같은 site_resource(예: naver) 는 한 번에 하나만 접속.
                #   락 순서는 항상 stage_sem → site_sem 으로 일정 → 데드락 없음.
                site_lock = (self.site_sems.get(site_key)
                             if (site_key and stage in self.site_access_stages) else None)
                if site_lock:
                    log(f"  [{tag}] [{stage}] 사이트 자원 '{site_key}' 대기/획득")
                    site_lock.acquire()
                try:
                    self.set_stage_status(unit, stage, 'RUNNING')
                    ok = self._run_cmds(tag, stage, cmds)
                finally:
                    if site_lock:
                        site_lock.release()
                if not ok:
                    self.set_stage_status(unit, stage, 'ERROR', f'{stage} 실패')
                    log(f"  [{tag}] [{stage}] 실패 → 유닛 중단", "ERROR")
                    return False
                self.set_stage_status(unit, stage, 'DONE')
        return True

    def _run_cmds(self, tag: str, stage: str, cmds: List[List[str]]) -> bool:
        for cmd in cmds:
            name = os.path.basename(cmd[1]) if len(cmd) > 1 else cmd[0]
            log(f"  [{tag}] [{stage}] 실행: {name}")
            if self.dry_run:
                log(f"    [DRY-RUN] {' '.join(cmd)}")
                continue
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUTF8'] = '1'
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', errors='replace', bufsize=1, env=env)
                if p.stdout:
                    for line in iter(p.stdout.readline, ''):
                        if line.strip():
                            log(f"      {line.rstrip()}")
                p.wait()
                if p.returncode != 0:
                    log(f"    [{name}] exit={p.returncode}", "ERROR")
                    return False
            except Exception as e:
                log(f"    [{name}] 예외: {e}", "ERROR")
                return False
        return True

    def finish_batch(self):
        # finish_batch 는 모든 유닛 성공 시에만 호출됨(run() 의 any_failed 분기) → 완료 유닛 = 전체.
        #   (옛 버전은 stage IN(NEW마지막,STOCK마지막) 카운트라 track/혼합 실행 시 오집계 + stage_plan 키 의존 → 제거)
        done = len(self.units)
        conn = self._db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE pipeline_batches SET status='COMPLETED', end_time=NOW(), success_brands=%s WHERE batch_id=%s",
                    (done, self.batch_id))
            conn.commit()
        finally:
            conn.close()
        log("=" * 60)
        log(f"배치 완료: {self.batch_id} (완료 유닛 {done}/{len(self.units)})")
        log("=" * 60)

    def mark_batch_failed(self):
        if not self.batch_id:
            return
        try:
            conn = self._db()
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE pipeline_batches SET status='FAILED', end_time=NOW() WHERE batch_id=%s",
                                (self.batch_id,))
                conn.commit()
            finally:
                conn.close()
            log(f"배치 실패 처리: {self.batch_id}", "ERROR")
        except Exception as e:
            log(f"배치 실패 처리 오류: {e}", "ERROR")
