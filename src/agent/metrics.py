"""탐지기 1단계: SQLite → 세그먼트×기간별 지표 집계. 전부 결정론적 코드.

입력은 data/funnel.db 하나뿐이다. 정답 파일·생성기 쪽 코드는 참조 금지 — score.py가 정적 검사한다.

귀속 규칙: 모든 이벤트(지원·매칭)는 '공고 코호트'로 귀속한다.
즉 기간(baseline/current)과 요일(평일/주말)은 공고의 posted_at 기준이다.
이유: 전환율 = 매칭/지원 을 같은 코호트 위에서 계산해야 분자·분모가 어긋나지 않는다.
(지원 시각 기준으로 나누면 기간 경계에서 매칭이 다른 기간으로 새어 나간다)
"""
import sqlite3
from collections import defaultdict
from datetime import datetime

from src import contract

MATCH_1H_MINUTES = 60.0


def _day_type(dt):
    return "주말" if dt.weekday() >= 5 else "평일"


def _period(dt):
    baseline_end = datetime.fromisoformat(contract.BASELINE_END)
    return "baseline" if dt.date() <= baseline_end.date() else "current"


def _segments_for(region, job, day_type, band):
    """한 이벤트가 속하는 (축, 세그먼트 값) 목록. 'overall'은 전체 대조군."""
    return [
        ("overall", "전체"),
        (contract.AXIS_REGION, region),
        (contract.AXIS_JOB, job),
        (contract.AXIS_WEEKDAY, day_type),
        (contract.AXIS_DISTANCE, band),
        (contract.AXIS_JOB_DAYTYPE, contract.job_daytype_value(job, day_type)),
    ]


def compute_metrics(db_path):
    """반환: {(axis, value, period): {지표·건수 dict}}"""
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT p.region, p.job_category, p.posted_at,
               a.worker_id, a.applied_at, a.distance_km,
               m.matched_at, m.no_show
        FROM applications a
        JOIN postings p ON p.id = a.posting_id
        LEFT JOIN matches m ON m.application_id = a.id
    """).fetchall()
    postings = conn.execute("SELECT region, job_category, posted_at FROM postings").fetchall()
    conn.close()

    acc = defaultdict(lambda: {"postings": 0, "applications": 0, "matches": 0,
                               "match_1h": 0, "noshow": 0})
    # 재이용률용: 세그먼트·기간별 worker 매칭 횟수. 사람 단위 지표라
    # 사람이 고정되는 축(지역/직종/전체)에서만 계산한다 — 거리/요일 축의 재이용은 해석이 안 된다.
    rehire_axes = {"overall", contract.AXIS_REGION, contract.AXIS_JOB}
    worker_matches = defaultdict(lambda: defaultdict(int))

    for region, job, posted_at, _ in [(r, j, t, None) for r, j, t in postings]:
        dt = datetime.fromisoformat(posted_at)
        for axis, value in _segments_for(region, job, _day_type(dt), None):
            if axis != contract.AXIS_DISTANCE:  # 거리 구간은 지원 이전엔 정의되지 않는다
                acc[(axis, value, _period(dt))]["postings"] += 1

    for region, job, posted_at, worker, applied_at, km, matched_at, no_show in rows:
        posted_dt = datetime.fromisoformat(posted_at)
        period = _period(posted_dt)
        day_type = _day_type(posted_dt)
        band = contract.distance_band(km)
        for axis, value in _segments_for(region, job, day_type, band):
            a = acc[(axis, value, period)]
            a["applications"] += 1
            if matched_at is not None:
                a["matches"] += 1
                delay_min = (datetime.fromisoformat(matched_at)
                             - datetime.fromisoformat(applied_at)).total_seconds() / 60.0
                if delay_min <= MATCH_1H_MINUTES:
                    a["match_1h"] += 1
                if no_show:
                    a["noshow"] += 1
                if axis in rehire_axes:
                    worker_matches[(axis, value, period)][worker] += 1

    # 건수 → 비율 지표. 분모가 0이면 지표는 None (후속 규칙에서 제외됨)
    out = {}
    for key, a in acc.items():
        m = dict(a)
        m[contract.METRIC_CONVERSION] = a["matches"] / a["applications"] if a["applications"] else None
        m[contract.METRIC_MATCH_1H] = a["match_1h"] / a["matches"] if a["matches"] else None
        m[contract.METRIC_NOSHOW] = a["noshow"] / a["matches"] if a["matches"] else None
        wm = worker_matches.get(key)
        if wm:
            matched_workers = len(wm)
            repeaters = sum(1 for c in wm.values() if c >= 2)
            m["matched_workers"] = matched_workers
            m[contract.METRIC_REHIRE] = repeaters / matched_workers
        else:
            m["matched_workers"] = 0
            m[contract.METRIC_REHIRE] = None
        out[key] = m
    return out


# 지표별 표본 크기(최소 표본 필터·영향 규모 산정에 쓰는 분모)가 무엇인지의 매핑
DENOM_FIELD = {
    contract.METRIC_CONVERSION: "applications",
    contract.METRIC_MATCH_1H: "matches",
    contract.METRIC_NOSHOW: "matches",
    contract.METRIC_REHIRE: "matched_workers",
}
