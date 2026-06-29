"""
네이버 쇼핑검색광고 자동입찰 엔진 v5
- 설정 소스: 구글 스프레드시트
- 입찰 단위: 소재(ad) — PUT /ncc/ads/{nccAdId}?fields=adAttr
- estimate:  소재ID 기반 — POST /estimate/average-position-bid/ad
"""

import logging
import time
from datetime import datetime
from typing import Optional, Dict

from naver_ad_client import NaverAdClient
from bid_calculator import BidConfig, EstimateData, calculate_optimal_bid
from sheets_config import SheetsConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bidder.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


class ShoppingBidder:

    def __init__(self, client: NaverAdClient, base_config: BidConfig, sheets: SheetsConfig):
        self.client      = client
        self.base_config = base_config   # step, safety_margin 등 공통 설정
        self.sheets      = sheets

    # ── estimate 조회 ──────────────────────────────────────────────

    def get_estimate(self, ad_id: str) -> Optional[EstimateData]:
        """
        POST /estimate/average-position-bid/ad
        쇼핑 소재(ad) 기준 순위별 평균 입찰가
        """
        try:
            resp = self.client.post(
                "/estimate/average-position-bid/ad",
                body={"nccAdId": ad_id},
            )
            pc = {item["rank"]: item["bidAmt"] for item in resp.get("pcList", [])}
            mo = {item["rank"]: item["bidAmt"] for item in resp.get("mobileList", [])}
            if pc or mo:
                return EstimateData(pc=pc, mo=mo)
        except Exception as e:
            log.debug(f"estimate 실패 ({ad_id}): {e}")
        return None

    # ── 입찰가 변경 ────────────────────────────────────────────────

    def update_ad_bid(self, ad_id: str, new_bid: int) -> bool:
        """
        PUT /ncc/ads/{nccAdId}?fields=adAttr
        body: {"adAttr": {"bidAmt": N, "useGroupBidAmt": false}}
        """
        try:
            self.client.put(
                f"/ncc/ads/{ad_id}",
                body={"adAttr": {"bidAmt": new_bid, "useGroupBidAmt": False}},
                params={"fields": "adAttr"},
            )
            return True
        except Exception as e:
            log.error(f"입찰가 변경 실패 ({ad_id}): {e}")
            return False

    # ── 메인 실행 ──────────────────────────────────────────────────

    def run_once(self, dry_run: bool = False):
        label = "[DRY RUN] " if dry_run else ""
        log.info(f"{label}입찰 사이클 시작")

        # 스프레드시트에서 소재 설정 로드
        ad_configs = self.sheets.load_ad_configs()
        if not ad_configs:
            log.warning("처리할 소재 없음 — 스프레드시트를 확인하세요")
            return

        ok = fail = skip = 0

        for cfg in ad_configs:
            try:
                result = self._process_one(cfg, dry_run)
                if result["changed"]:
                    ok += 1
                else:
                    skip += 1
            except Exception as e:
                fail += 1
                log.error(f"  소재 처리 오류 ({cfg['ad_id']}): {e}")
                self.sheets.write_error(cfg["row"], str(e))

            time.sleep(0.3)   # API rate limit 방어

        log.info(f"사이클 완료 — 변경:{ok} / 유지:{skip} / 오류:{fail}")

    def _process_one(self, cfg: Dict, dry_run: bool) -> Dict:
        """소재 1개 처리"""
        ad_id       = cfg["ad_id"]
        ad_name     = cfg["ad_name"] or ad_id
        keyword     = cfg["keyword"]        # 메모용 (로그 출력)
        target_rank = cfg["target_rank"]
        max_cpc     = cfg["max_cpc"]
        current_bid = cfg["current_bid"]

        # 소재별 BidConfig 구성 (max_cpc만 소재마다 다름)
        config = BidConfig(
            max_cpc=max_cpc,
            min_bid=self.base_config.min_bid,
            step=self.base_config.step,
            default_target_rank=target_rank,
            safety_margin=self.base_config.safety_margin,
            adjust_ratio=self.base_config.adjust_ratio,
        )

        # estimate 조회
        estimate = self.get_estimate(ad_id)
        if estimate:
            pc_bids = {r: b for r, b in estimate.pc.items()}
            log.debug(f"  estimate PC: {pc_bids}")

        # 최적 입찰가 계산
        new_bid, reason = calculate_optimal_bid(
            adgroup_name=ad_id,   # 소재 단위이므로 ad_id를 키로 사용
            current_bid=current_bid,
            config=config,
            estimate=estimate,
            current_rank=None,    # 소재 단위 실시간 순위는 stats API 별도 조회 필요
            device="pc",
        )

        changed = new_bid != current_bid
        success = True

        if changed and not dry_run:
            success = self.update_ad_bid(ad_id, new_bid)

        # 스프레드시트에 결과 기록
        if not dry_run:
            self.sheets.write_result(cfg["row"], current_bid, new_bid)

        action = "변경" if changed else "유지"
        status = "✓" if success else "✗"
        log.info(
            f"  [{action}]{status} [{ad_name}] 키워드:'{keyword}' "
            f"목표:{target_rank}위 | {current_bid}→{new_bid}원 | {reason}"
        )

        return {"changed": changed, "success": success}


# ── 스케줄러 ──────────────────────────────────────────────────────

def run_scheduler(bidder: ShoppingBidder, interval_minutes: int = 60, dry_run: bool = False):
    log.info(f"스케줄러 시작 ({interval_minutes}분 간격)")
    while True:
        try:
            bidder.run_once(dry_run=dry_run)
        except Exception as e:
            log.error(f"사이클 오류: {e}", exc_info=True)
        log.info(f"{interval_minutes}분 대기 중...")
        time.sleep(interval_minutes * 60)


# ── 진입점 ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os

    client = NaverAdClient(
        api_key=os.getenv("NAVER_AD_API_KEY", "여기에_액세스라이선스"),
        secret_key=os.getenv("NAVER_AD_SECRET_KEY", "여기에_비밀키"),
        customer_id=os.getenv("NAVER_AD_CUSTOMER_ID", "여기에_고객ID"),
    )

    # 공통 설정 (max_cpc는 시트에서 소재별로 읽음)
    base_config = BidConfig(
        max_cpc=0,          # 더미값 — 실제 값은 시트에서 행마다 읽음
        min_bid=70,         # 쇼핑몰상품형 최소 70원
        step=10,
        safety_margin=0.95,
        adjust_ratio=0.10,
    )

    sheets = SheetsConfig(
        spreadsheet_id="여기에_스프레드시트_ID",   # URL의 /d/{ID}/ 부분
        sheet_name="자동입찰설정",
        credentials_path="service_account.json",   # 서비스 계정 키 파일
    )

    bidder = ShoppingBidder(client=client, base_config=base_config, sheets=sheets)

    # 테스트 (시트 기록 없음, 입찰가 변경 없음)
    # bidder.run_once(dry_run=True)

    # 운영: 1시간마다 실행
    run_scheduler(bidder, interval_minutes=60, dry_run=False)
