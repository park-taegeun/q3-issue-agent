"""탐지기 3단계: 후보 심각도 점수화 → dedup → 단 하나로 좁힌다. 전부 결정론적 코드.

점수 = 상대 악화 × 볼륨 비중 × 지표 가중치
- 상대 악화: 얼마나 곪았나
- 볼륨 비중: 얼마나 큰 부위인가 (같은 지표 분모 대비 비중이라 단위 일관)
- 지표 가중치: 어떤 종류의 훼손인가 — 이 가중치는 '판단'이므로 proposal에 그대로 노출해
  리뷰 가능하게 한다 (LLM/사람이 수치를 임의 조정하지 못하게 코드 상수로 고정)

dedup 0 (표준화 검사): 한 축의 진짜 문제는 다른 축에도 '구성비 효과'로 새어 보인다.
(예: 원거리 매칭 비중이 큰 지역은 원거리 노쇼 급등만으로도 지역 노쇼가 올라 보인다)
후보 세그먼트의 current 실제값을, 그 세그먼트의 통제축 구성비 × 전체 통제축별 비율로
만든 기대값과 비교해, 차이가 임계 미만이면 "구성비로 설명됨"으로 폐기한다.
multi-seed 평가에서 실측된 오탐 2건(FAILURES.md 2026-07-30 #2 사례)이 이 규칙의 근거다.
"""
from src import contract
from src.agent.rules import ABS_MIN

# 노쇼: 이미 성사된 약속의 파기 — 양측 신뢰를 직접 훼손, 회복이 가장 어렵다
# 재이용: 장기 리텐션 지표 — 훼손이 조용히 누적된다
# 전환: 퍼널 핵심이지만 원인이 다양(수급 불균형 포함)해 신뢰 훼손보다 한 단계 낮게
# 1시간 내 매칭: 속도 품질 — 중요하지만 위 셋보다 후행
METRIC_WEIGHT = {
    contract.METRIC_NOSHOW: 1.5,
    contract.METRIC_REHIRE: 1.3,
    contract.METRIC_CONVERSION: 1.2,
    contract.METRIC_MATCH_1H: 1.0,
}

# 퍼널 상류 → 하류. 같은 세그먼트에서 동시 악화하면 상류를 원인 후보로 대표시킨다.
# (예: 매칭 급감 세그먼트의 재이용률 하락은 매칭 총량 감소의 기계적 파생 — FAILURES.md 참고)
FUNNEL_ORDER = [contract.METRIC_CONVERSION, contract.METRIC_MATCH_1H,
                contract.METRIC_NOSHOW, contract.METRIC_REHIRE]


def _score(c):
    return c["rel_worsening"] * c["volume_share"] * METRIC_WEIGHT[c["metric"]]


# 이벤트가 후보 세그먼트에 속하는가 — 축별 판정
def _in_segment(e, axis, value):
    if axis == contract.AXIS_REGION:
        return e["region"] == value
    if axis == contract.AXIS_JOB:
        return e["job"] == value
    if axis == contract.AXIS_WEEKDAY:
        return e["day_type"] == value
    if axis == contract.AXIS_DISTANCE:
        return e["band"] == value
    if axis == contract.AXIS_JOB_DAYTYPE:
        job, day_type = value.split("+")
        return e["job"] == job and e["day_type"] == day_type
    raise ValueError(axis)


# 표준화 검사에서 통제할 축: 후보 자신의 축(및 2차원 축의 성분)은 제외
_CONTROL_AXES = {
    contract.AXIS_REGION: [contract.AXIS_JOB, contract.AXIS_WEEKDAY, contract.AXIS_DISTANCE],
    contract.AXIS_JOB: [contract.AXIS_REGION, contract.AXIS_WEEKDAY, contract.AXIS_DISTANCE],
    contract.AXIS_WEEKDAY: [contract.AXIS_REGION, contract.AXIS_JOB, contract.AXIS_DISTANCE],
    contract.AXIS_DISTANCE: [contract.AXIS_REGION, contract.AXIS_JOB, contract.AXIS_WEEKDAY],
    contract.AXIS_JOB_DAYTYPE: [contract.AXIS_REGION, contract.AXIS_DISTANCE],
}
_AXIS_FIELD = {contract.AXIS_REGION: "region", contract.AXIS_JOB: "job",
               contract.AXIS_WEEKDAY: "day_type", contract.AXIS_DISTANCE: "band"}


def _metric_parts(metric, e):
    """이벤트가 이 지표의 (분모, 분자)에 포함되는가."""
    if metric == contract.METRIC_CONVERSION:
        return True, e["matched"]
    if metric == contract.METRIC_MATCH_1H:
        return e["matched"], e["within_1h"]
    if metric == contract.METRIC_NOSHOW:
        return e["matched"], e["no_show"]
    raise ValueError(metric)


def composition_check(cand, events):
    """current 실제값 vs 통제축 구성비 기대값. 설명되면 (통제축, 기대값, 실제값) 반환.

    재이용률은 사람 단위 지표라 이벤트 구성비 표준화가 성립하지 않아 검사 대상에서 뺀다
    (재이용률 후보는 dedup 1의 퍼널 상류 규칙이 별도로 다룬다).
    """
    if cand["metric"] == contract.METRIC_REHIRE:
        return None
    cur = [e for e in events if e["period"] == "current"]
    seg = [e for e in cur if _in_segment(e, cand["axis"], cand["value"])]
    bad = contract.BAD_DIRECTION[cand["metric"]]

    seg_denom = seg_num = 0
    for e in seg:
        d, n = _metric_parts(cand["metric"], e)
        seg_denom += d
        seg_num += d and n
    if not seg_denom:
        return None
    actual = seg_num / seg_denom

    for control_axis in _CONTROL_AXES[cand["axis"]]:
        field = _AXIS_FIELD[control_axis]
        g_denom, g_num, s_denom = {}, {}, {}
        for e in cur:
            d, n = _metric_parts(cand["metric"], e)
            if d:
                g_denom[e[field]] = g_denom.get(e[field], 0) + 1
                g_num[e[field]] = g_num.get(e[field], 0) + (1 if n else 0)
        for e in seg:
            d, n = _metric_parts(cand["metric"], e)
            if d:
                s_denom[e[field]] = s_denom.get(e[field], 0) + 1
        # 기대값 = Σ (세그먼트 내 통제축 값별 비중 × 전체의 그 값 비율)
        expected = sum(s_denom[v] * (g_num[v] / g_denom[v]) for v in s_denom) / seg_denom
        if bad * (actual - expected) < ABS_MIN:
            return control_axis, expected, actual
    return None


def select_one(candidates, events):
    """후보들 → (선택 1개, 폐기 목록[사유 포함]). 정렬 키를 고정해 결정론을 보장한다."""
    for c in candidates:
        c["score"] = round(_score(c), 4)
        c["score_parts"] = {"rel_worsening": round(c["rel_worsening"], 3),
                            "volume_share": round(c["volume_share"], 3),
                            "metric_weight": METRIC_WEIGHT[c["metric"]]}
    discarded = []

    # dedup 0: 표준화 검사 — 다른 축의 구성비로 설명되는 악화는 세그먼트 고유 문제가 아니다
    alive = []
    for c in sorted(candidates, key=lambda c: (-c["score"], c["axis"], c["value"], c["metric"])):
        explained = composition_check(c, events)
        if explained is None:
            alive.append(c)
        else:
            control_axis, expected, actual = explained
            c["discard_reason"] = (f"{control_axis} 구성비로 설명되는 악화 — 기대값 "
                                   f"{expected:.3f} vs 실제 {actual:.3f}, 차이가 임계(5%p) 미만이라 "
                                   f"세그먼트 고유 문제로 볼 수 없음")
            discarded.append(c)

    # dedup 1: 같은 세그먼트에서 다중 지표 악화 → 퍼널 최상류 지표만 대표로 남긴다
    kept, seen_segment = [], {}
    for c in alive:
        key = (c["axis"], c["value"])
        if key not in seen_segment:
            seen_segment[key] = [c]
        else:
            seen_segment[key].append(c)
    for key, group in seen_segment.items():
        group.sort(key=lambda c: FUNNEL_ORDER.index(c["metric"]))
        kept.append(group[0])
        for c in group[1:]:
            c["discard_reason"] = (f"동일 세그먼트의 상류 지표({group[0]['metric']}) 악화에 따른 "
                                   f"기계적 파생 가능성 — 대표 후보에 흡수")
            discarded.append(c)

    # dedup 2: 2차원 축 후보와 그 성분 축 후보가 같은 지표면 점수 높은 쪽만 남긴다
    # (주방+주말이 곪으면 '주방' 단독, '주말' 단독 축에도 희석된 악화가 새어 보인다)
    final = []
    for c in sorted(kept, key=lambda c: (-c["score"], c["axis"], c["value"], c["metric"])):
        dup = None
        for f in final:
            pair = {c["axis"], f["axis"]}
            if c["metric"] == f["metric"] and contract.AXIS_JOB_DAYTYPE in pair:
                two_d, one_d = (c, f) if c["axis"] == contract.AXIS_JOB_DAYTYPE else (f, c)
                if one_d["axis"] in (contract.AXIS_JOB, contract.AXIS_WEEKDAY) \
                        and one_d["value"] in two_d["value"].split("+"):
                    dup = f
        if dup is None:
            final.append(c)
        else:
            c["discard_reason"] = (f"상위 점수 후보({dup['axis']}={dup['value']})와 동일 현상의 "
                                   f"중복 관측(축만 다름) — 희석/집중 관계")
            discarded.append(c)

    if not final:
        return None, discarded
    winner = final[0]
    for c in final[1:]:
        c["discard_reason"] = (f"심각도 점수 열세 ({c['score']} vs 1위 {winner['score']}) — "
                               f"상대악화 {c['score_parts']['rel_worsening']}, "
                               f"볼륨비중 {c['score_parts']['volume_share']}, "
                               f"가중치 {c['score_parts']['metric_weight']}")
        discarded.append(c)
    return winner, discarded
