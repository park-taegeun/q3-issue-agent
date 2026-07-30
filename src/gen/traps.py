"""함정 주입기. current 기간의 모든 '변화'는 여기서만 만든다.

구조: 표면 개선(전체 지표를 좋아 보이게) + 곪은 세그먼트 3곳(핵심 함정).
각 함정은 "집계는 정상/개선처럼 보이는데 특정 세그먼트가 악화"여야 한다.

⚠ 격리: 이 파일과 answers/ground_truth.json은 탐지기(src/agent/)가
절대 임포트/참조하면 안 된다. score.py가 실행 시 이를 정적 검사한다.
"""
import json
import os

from src import contract
from src.gen.generate import BaseMods

# ── 표면 개선 스토리 (current 전체): "매칭 반경 완화 + 매칭 시스템 개선" ──
SURFACE_DELAY_FACTOR = 0.85    # 매칭 지연 -15% → 전체 1시간 내 매칭률 개선
SURFACE_FILL_FACTOR = 1.08     # 채용 성사 +8% → 전체 매칭 수 증가
SURFACE_NOSHOW_DROP = 0.01     # 근거리 노쇼 소폭 개선 (리마인더 알림 개선 가정)

# ── T1 (primary): 반경 완화로 원거리 매칭이 늘었는데 그 구간 노쇼가 곪는다 ──
T1_BAND_WEIGHTS_CURRENT = {"0-1km": 0.40, "1-3km": 0.31, "3-5km": 0.15, "5km+": 0.14}
T1_FAR_NOSHOW_CURRENT = 0.28   # baseline 5km+ 노쇼 0.12 → 0.28

# ── T2: 야탑동 — 지원은 몰리는데 채용 성사가 무너진다 (전환율 붕괴) ──
T2_REGION = "야탑동"
T2_APPS_FACTOR = 1.45          # 지원 수 +45% (표면상 '활성화'처럼 보임)
T2_FILL_FACTOR = 0.45          # 성사율 ×0.45 (표면 개선 ×1.08 이후 적용 → 실질 ×0.49)

# ── T3: 주방×주말 — 급구 대응 실패로 1시간 내 매칭률 붕괴 ──
T3_JOB, T3_DAY_TYPE = "주방", "주말"
T3_DELAY_FACTOR = 4.5          # 지연 평균 70분 → 315분, 1시간 내 매칭률 57% → ~17%


class TrapMods(BaseMods):
    """current 기간에만 개입한다. baseline은 베이스 그대로 → 기간 비교가 함정을 드러낸다."""

    def band_weights(self, period, weights):
        if period == "current":
            return dict(T1_BAND_WEIGHTS_CURRENT)   # T1: 반경 완화 → 원거리 지원 비중 확대
        return weights

    def apps_mean(self, period, region, job, day_type, mean):
        if period == "current" and region == T2_REGION:
            return mean * T2_APPS_FACTOR           # T2: 야탑동 지원 급증
        return mean

    def fill_prob(self, period, region, job, day_type, p):
        if period == "current":
            p *= SURFACE_FILL_FACTOR               # 표면: 전체 매칭 수 증가
            if region == T2_REGION:
                p *= T2_FILL_FACTOR                # T2: 야탑동 성사율 붕괴
        return p

    def delay_mean(self, period, region, job, day_type, mean):
        if period == "current":
            if job == T3_JOB and day_type == T3_DAY_TYPE:
                return mean * T3_DELAY_FACTOR      # T3: 주방×주말 매칭 지연 폭증
            return mean * SURFACE_DELAY_FACTOR     # 표면: 나머지는 전부 빨라짐
        return mean

    def noshow_prob(self, period, region, job, day_type, band, p):
        if period == "current":
            if band == "5km+":
                return T1_FAR_NOSHOW_CURRENT       # T1: 원거리 노쇼 급등
            return max(0.0, p - SURFACE_NOSHOW_DROP)  # 표면: 근거리는 소폭 개선
        return p


def write_ground_truth(path, seed):
    """심은 함정의 정답을 기록한다. 채점(score.py) 전용 — 탐지기는 이 파일을 모른다."""
    truth = {
        "seed": seed,
        "surface_story": ("current 기간 전체: 매칭 지연 x0.85, 채용 성사 x1.08, 근거리 노쇼 -1%p "
                          "— 집계 지표(매칭 수, 1시간 내 매칭률, 전체 노쇼율)는 개선/유지로 보인다"),
        "primary": "T1_distance_noshow",
        "traps": [
            {
                "id": "T1_distance_noshow",
                "axis": contract.AXIS_DISTANCE, "value": "5km+",
                "metric": contract.METRIC_NOSHOW, "direction": "up",
                "severity": 3,
                "planted": {"noshow": "0.12 → 0.28", "band_share": "0.07 → 0.14"},
                "why_severe": ("노쇼는 구인자·구직자 양쪽 신뢰를 깎는 지표인데, 반경 완화로 "
                               "이 구간의 매칭 비중 자체가 2배로 커지는 중 — 방치하면 전체로 번진다"),
            },
            {
                "id": "T2_region_conversion",
                "axis": contract.AXIS_REGION, "value": T2_REGION,
                "metric": contract.METRIC_CONVERSION, "direction": "down",
                "severity": 2,
                "planted": {"apps": "x1.45", "fill": "x0.49(실질)"},
                "why_severe": "지원자가 몰리는데 성사가 안 되면 해당 동네 구직자 이탈로 직결된다",
            },
            {
                "id": "T3_kitchen_weekend_1h",
                "axis": contract.AXIS_JOB_DAYTYPE,
                "value": contract.job_daytype_value(T3_JOB, T3_DAY_TYPE),
                "metric": contract.METRIC_MATCH_1H, "direction": "down",
                "severity": 2,
                "planted": {"delay_mean": "70분 → 315분", "match_1h": "~0.57 → ~0.17"},
                "why_severe": "주말 주방은 급구 수요 — 1시간 내 매칭이 안 되면 공고 자체가 무의미해진다",
            },
        ],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(truth, f, ensure_ascii=False, indent=2)
    return truth
