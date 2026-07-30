"""탐지기 2단계: "표면 vs 세그먼트" 괴리 스캔. 전부 결정론적 코드.

핵심 아이디어: 곪은 세그먼트는 집계에도 일부 새어 나오므로, '전체 지표가 멀쩡한가'가
아니라 '세그먼트 악화가 전체 악화를 얼마나 초과하는가(excess)'를 본다.

임계값의 근거 (FAILURES.md의 순환 방지 기록 참고 — 심은 정답이 아니라 제품 기준에서 도출):
- ABS_MIN 5pp: 매칭 퍼널에서 5pp는 이미 액션을 요하는 크기 (노쇼 5pp = 매칭 20건당 파기 1건 추가)
- EXCESS_MIN 5pp: 전체 추세로 설명되는 만큼은 세그먼트 고유 문제가 아니다
- REL_MIN 30%: 절대폭이 커도 baseline이 큰 지표면 체감이 다르다 — 상대 기준 병행
- MIN_N 50: 노이즈 통제는 임계값이 아니라 표본 조건의 역할. n=50이면 p≈0.1 지표의
  두 기간 차이 표준오차 ≈ 6pp — 세 조건 동시 통과가 노이즈로는 어렵게 된다
"""
from src import contract
from src.agent.metrics import DENOM_FIELD

ABS_MIN = 0.05      # 세그먼트 악화 절대폭 하한 (5pp)
EXCESS_MIN = 0.05   # 전체 악화 대비 초과 악화 하한 (5pp)
REL_MIN = 0.30      # 상대 악화 하한 (30%)
REL_FLOOR = 0.02    # 상대 악화 분모 하한 — baseline≈0일 때 상대값 폭주 방지
MIN_N = 50          # 두 기간 모두 지표 분모 최소 표본


def scan(metrics):
    """세그먼트×지표 전수 스캔 → 후보 리스트. 점수/선택은 severity.py 몫."""
    candidates = []
    segments = sorted({(axis, value) for (axis, value, _p) in metrics
                       if axis != "overall"})
    for axis, value in segments:
        base = metrics.get((axis, value, "baseline"))
        cur = metrics.get((axis, value, "current"))
        if not base or not cur:
            continue
        for metric in contract.RATE_METRICS:
            b, c = base.get(metric), cur.get(metric)
            if b is None or c is None:
                continue
            denom = DENOM_FIELD[metric]
            n_b, n_c = base[denom], cur[denom]
            if n_b < MIN_N or n_c < MIN_N:
                continue  # 표본 부족 세그먼트는 판단 보류 — 오탐 통제의 1차 방어선

            bad = contract.BAD_DIRECTION[metric]
            seg_worsening = bad * (c - b)          # +면 악화, -면 개선
            ob = metrics[("overall", "전체", "baseline")][metric]
            oc = metrics[("overall", "전체", "current")][metric]
            overall_worsening = bad * (oc - ob)
            excess = seg_worsening - overall_worsening
            rel = seg_worsening / max(b, REL_FLOOR)

            if seg_worsening >= ABS_MIN and excess >= EXCESS_MIN and rel >= REL_MIN:
                overall_n_c = metrics[("overall", "전체", "current")][denom]
                candidates.append({
                    "axis": axis, "value": value, "metric": metric,
                    "direction": "up" if c > b else "down",
                    "baseline": b, "current": c,
                    "n_baseline": n_b, "n_current": n_c,
                    "overall_baseline": ob, "overall_current": oc,
                    "seg_worsening": seg_worsening, "excess": excess,
                    "rel_worsening": rel,
                    # 영향 규모: 같은 지표 분모끼리의 비중이라 지표 간 비교가 공정하다
                    "volume_share": n_c / overall_n_c if overall_n_c else 0.0,
                })
    return candidates
