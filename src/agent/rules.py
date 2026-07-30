"""탐지기 2단계: "표면 vs 세그먼트" 괴리 스캔. 전부 결정론적 코드.

핵심 아이디어: 곪은 세그먼트는 집계에도 일부 새어 나오므로, '전체 지표가 멀쩡한가'가
아니라 '세그먼트 악화가 전체 악화를 얼마나 초과하는가(excess)'를 본다.

임계값의 근거 (FAILURES.md의 순환 방지 기록 참고 — 심은 정답이 아니라 제품 기준에서 도출):
- ABS_MIN 5pp: 매칭 퍼널에서 5pp는 이미 액션을 요하는 크기 (노쇼 5pp = 매칭 20건당 파기 1건 추가)
- EXCESS_MIN 5pp: 전체 추세로 설명되는 만큼은 세그먼트 고유 문제가 아니다
- REL_MIN 30%: 절대폭이 커도 baseline이 큰 지표면 체감이 다르다 — 상대 기준 병행
- MIN_N 50: 두 기간 모두 지표 분모 최소 표본 — 판단 보류선
- 유의성(BH/FDR): 위 조건만으로는 부족했다. 세그먼트×지표로 회당 100건 넘게 검정하므로
  2.5~3σ 우연이 매 실행 몇 개씩 나온다 — 클린 데이터 참음성 검사에서 10시드 중 5회
  헛제안으로 실측 (FAILURES.md 2026-07-30 #3). 2-비율 z-검정의 단측 p값에
  Benjamini-Hochberg(α=0.05) 보정을 적용한다. Bonferroni가 아닌 BH인 이유:
  Bonferroni(FWER)로는 경계 신호가 대거 미탐됨을 실측(primary 적중 14/15→9/15),
  '발견' 문제의 표준은 FDR 통제다. 신호가 없는 데이터에서 BH의 최솟값 검정은
  Bonferroni와 같아져 헛제안 0이 유지된다 (DECISIONS.md 참고).
"""
import math

from src import contract
from src.agent.metrics import DENOM_FIELD, NUM_FIELD

ABS_MIN = 0.05      # 세그먼트 악화 절대폭 하한 (5pp)
EXCESS_MIN = 0.05   # 전체 악화 대비 초과 악화 하한 (5pp)
REL_MIN = 0.30      # 상대 악화 하한 (30%)
REL_FLOOR = 0.02    # 상대 악화 분모 하한 — baseline≈0일 때 상대값 폭주 방지
MIN_N = 50          # 두 기간 모두 지표 분모 최소 표본
ALPHA = 0.05        # 다중 비교 보정 전 유의수준


def _one_sided_p(x_b, n_b, x_c, n_c, bad):
    """2-비율 z-검정(합동 분산)의 단측 p값 — '악화 방향' 쪽만 본다."""
    pooled = (x_b + x_c) / (n_b + n_c)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_b + 1 / n_c))
    if se == 0:
        return 1.0
    z = bad * (x_c / n_c - x_b / n_b) / se
    return 0.5 * math.erfc(z / math.sqrt(2))  # P(Z >= z)


def scan(metrics):
    """세그먼트×지표 전수 스캔 → 후보 리스트. 점수/선택은 severity.py 몫."""
    # 1차: 표본 조건을 넘는 (세그먼트, 지표) 검정 대상을 모두 모은다.
    # 검정 수를 먼저 세어야 Bonferroni 보정 임계(α/검정 수)를 정할 수 있다.
    tests = []
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
            if base[denom] < MIN_N or cur[denom] < MIN_N:
                continue  # 표본 부족 세그먼트는 판단 보류 — 오탐 통제의 1차 방어선
            tests.append((axis, value, metric, base, cur))

    # BH(FDR) 임계: 전체 검정의 p값을 오름차순 정렬해 p_(k) ≤ k·α/m 를 만족하는
    # 가장 큰 k의 p_(k)를 컷오프로 쓴다. 강한 진짜 신호가 있으면 임계가 적응적으로
    # 올라가 경계 신호를 살리고, 신호가 없으면 Bonferroni 수준으로 조여진다.
    all_p = []
    for axis, value, metric, base, cur in tests:
        num = NUM_FIELD[metric]
        denom = DENOM_FIELD[metric]
        all_p.append(_one_sided_p(base[num], base[denom], cur[num], cur[denom],
                                  contract.BAD_DIRECTION[metric]))
    m = len(all_p)
    p_crit = 0.0
    for k, p in enumerate(sorted(all_p), start=1):
        if p <= k * ALPHA / m:
            p_crit = p

    # 2차: 제품 기준 임계 + 유의성 조건을 모두 넘는 것만 후보로 올린다
    candidates = []
    for i, (axis, value, metric, base, cur) in enumerate(tests):
        b, c = base[metric], cur[metric]
        denom, num = DENOM_FIELD[metric], NUM_FIELD[metric]
        n_b, n_c = base[denom], cur[denom]

        bad = contract.BAD_DIRECTION[metric]
        seg_worsening = bad * (c - b)          # +면 악화, -면 개선
        ob = metrics[("overall", "전체", "baseline")][metric]
        oc = metrics[("overall", "전체", "current")][metric]
        overall_worsening = bad * (oc - ob)
        excess = seg_worsening - overall_worsening
        rel = seg_worsening / max(b, REL_FLOOR)
        p_value = all_p[i]

        if seg_worsening >= ABS_MIN and excess >= EXCESS_MIN and rel >= REL_MIN \
                and p_value <= p_crit:
            overall_n_c = metrics[("overall", "전체", "current")][denom]
            candidates.append({
                "axis": axis, "value": value, "metric": metric,
                "direction": "up" if c > b else "down",
                "baseline": b, "current": c,
                "n_baseline": n_b, "n_current": n_c,
                "overall_baseline": ob, "overall_current": oc,
                "seg_worsening": seg_worsening, "excess": excess,
                "rel_worsening": rel,
                "p_value": p_value, "p_crit": p_crit, "n_tests": m,
                # 영향 규모: 같은 지표 분모끼리의 비중이라 지표 간 비교가 공정하다
                "volume_share": n_c / overall_n_c if overall_n_c else 0.0,
            })
    return candidates
