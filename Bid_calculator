"""
입찰가 계산 로직 v3 — 쇼핑검색광고 전용
- 입찰 단위: 광고그룹(adgroup) bidAmt
- estimate: /estimate/average-position-bid/ad (소재ID 기반)
- 목표: 광고그룹별 목표 순위 달성
"""

from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass
class BidConfig:
    max_cpc: int                            # CPC 절대 상한선 (원) — 필수
    min_bid: int = 70                       # 최소 입찰가 (쇼핑몰상품형 70원, 브랜드형 300원)
    step: int = 10                          # 입찰 단위
    default_target_rank: int = 3            # 기본 목표 순위
    safety_margin: float = 0.95            # 실제 상한 = max_cpc × safety_margin
    adjust_ratio: float = 0.10             # estimate 없을 때 점진 조정 비율

    # 광고그룹별 목표 순위 개별 지정
    # key = 광고그룹 이름 (nccAdgroupId 대신 사람이 읽기 쉬운 이름 사용)
    # 예: {"덴탈껌_그룹": 1, "간식_그룹": 3}
    adgroup_target_ranks: Dict[str, int] = field(default_factory=dict)

    def get_target_rank(self, adgroup_name: str) -> int:
        return self.adgroup_target_ranks.get(adgroup_name, self.default_target_rank)


@dataclass
class EstimateData:
    """
    POST /estimate/average-position-bid/ad 응답
    쇼핑검색광고 소재(NVMID) 기준 순위별 평균 입찰가
    """
    pc: Dict[int, int]    # {1: 64080, 2: 2720, 3: 2470, 4: 1770}
    mo: Dict[int, int]    # {1: 72960, 2: 2880, 3: 2310, 4: 2270}

    def avg_bid_for_rank(self, rank: int, device: str = "pc") -> Optional[int]:
        data = self.pc if device == "pc" else self.mo
        return data.get(rank)

    def needed_bid_for_rank(self, rank: int, device: str = "pc", step: int = 10) -> Optional[int]:
        """
        목표 순위 진입에 필요한 입찰가.
        해당 순위 평균가 + 1 step (안정적 진입)
        """
        avg = self.avg_bid_for_rank(rank, device)
        if avg is None:
            return None
        return avg + step


def calculate_optimal_bid(
    adgroup_name: str,
    current_bid: int,
    config: BidConfig,
    estimate: Optional[EstimateData] = None,
    current_rank: Optional[float] = None,
    device: str = "pc",
) -> tuple[int, str]:
    """
    쇼핑검색광고 광고그룹 최적 입찰가 계산

    우선순위:
    1. estimate 데이터 있음 → 순위별 평균 입찰가 직접 참조
    2. estimate 없음 + 현재 순위 있음 → ±adjust_ratio% 점진 조정
    3. 둘 다 없음 → 유지

    Returns:
        (new_bid, reason)
    """
    target_rank = config.get_target_rank(adgroup_name)
    ceiling = _ceiling(config)

    # ── 1. estimate 기반 (가장 정확) ─────────────────────────────
    if estimate is not None:
        needed = estimate.needed_bid_for_rank(target_rank, device, config.step)
        if needed is not None:
            new_bid = _clamp(needed, config.min_bid, ceiling, config.step)
            avg = estimate.avg_bid_for_rank(target_rank, device)
            reason = f"estimate 기반: {target_rank}위 평균가({avg}원)+{config.step}원 → {needed}원"
            return new_bid, reason

    # ── 2. 현재 순위 기반 점진 조정 ──────────────────────────────
    if current_rank is not None:
        rank_int = round(current_rank)
        if rank_int > target_rank:
            new_bid = _clamp(
                int(current_bid * (1 + config.adjust_ratio)),
                config.min_bid, ceiling, config.step, round_up=True
            )
            reason = f"순위 부족: 현재 {rank_int}위 > 목표 {target_rank}위 → +{config.adjust_ratio*100:.0f}%"
        elif rank_int < target_rank - 1:
            new_bid = max(
                _round_down(int(current_bid * (1 - config.adjust_ratio)), config.step),
                config.min_bid
            )
            reason = f"순위 여유: 현재 {rank_int}위, 목표 {target_rank}위 → -{config.adjust_ratio*100:.0f}%"
        else:
            new_bid = current_bid
            reason = f"유지: 현재 {rank_int}위 = 목표 {target_rank}위"
        return new_bid, reason

    # ── 3. 정보 없음 ──────────────────────────────────────────────
    return current_bid, "정보 없음: 유지"


# ── 헬퍼 ──────────────────────────────────────────────────────────

def _ceiling(config: BidConfig) -> int:
    return _round_down(int(config.max_cpc * config.safety_margin), config.step)

def _clamp(value: int, lo: int, hi: int, step: int, round_up: bool = False) -> int:
    v = max(lo, min(value, hi))
    return _round_up(v, step) if round_up else _round_down(v, step)

def _round_up(v: int, step: int) -> int:
    return ((v + step - 1) // step) * step

def _round_down(v: int, step: int) -> int:
    return (v // step) * step
