"""
네이버 쇼핑검색광고 자동입찰
깃헙 액션에서 1회 실행되는 구조 (스케줄러 없음)
"""

import os
import json
import logging
import time
import tempfile
from typing import Optional, Dict, List

from naver_ad_client import NaverAdClient
from bid_calculator import BidConfig, EstimateData, calculate_optimal_bid
from sheets_config import SheetsConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


class ShoppingBidder:

    def __init__(self, client: NaverAdClient, base_config: BidConfig, sheets: SheetsConfig):
        self.client      = client
        self.base_config = base_config
        self.sheets      = sheets

    def get_estimate(self, ad_id: str) -> Optional[EstimateData]:
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

    def update_ad_bid(self, ad_id: str, new_bid: int) -> bool:
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

    def run(self, dry_run: bool = False):
        label = "[DRY RUN] " if dry_run else ""
        log.info(f"{label}입찰 사이클 시작")

        ad_configs = self.sheets.load_ad_configs()
        if not ad_configs:
            log.warning("처리할 소재 없음 — 스프레드시트를 확인하세요")
            return

        ok = fail = skip = 0

        for cfg in ad_configs:
            try:
                changed = self._process_one(cfg, dry_run)
                if changed:
                    ok += 1
                else:
                    skip += 1
            except Exception as e:
                fail += 1
                log.error(f"소재 처리 오류 ({cfg['ad_id']}): {e}")
                self.sheets.write_error(cfg["row"], str(e))
            time.sleep(0.3)

        log.info(f"완료 — 변경:{ok} / 유지:{skip} / 오류:{fail}")

    def _process_one(self, cfg: Dict, dry_run: bool) -> bool:
        ad_id       = cfg["ad_id"]
        ad_name     = cfg["ad_name"] or ad_id
        target_rank = cfg["target_rank"]
        max_cpc     = cfg["max_cpc"]
        current_bid = cfg["current_bid"]

        config = BidConfig(
            max_cpc=max_cpc,
            min_bid=self.base_config.min_bid,
            step=self.base_config.step,
            default_target_rank=target_rank,
            safety_margin=self.base_config.safety_margin,
            adjust_ratio=self.base_config.adjust_ratio,
        )

        estimate = self.get_estimate(ad_id)
        new_bid, reason = calculate_optimal_bid(
            adgroup_name=ad_id,
            current_bid=current_bid,
            config=config,
            estimate=estimate,
            current_rank=None,
            device="pc",
        )

        changed = new_bid != current_bid
        success = True
        if changed and not dry_run:
            success = self.update_ad_bid(ad_id, new_bid)

        if not dry_run:
            self.sheets.write_result(cfg["row"], current_bid, new_bid)

        action = "변경" if changed else "유지"
        status = "✓" if success else "✗"
        log.info(f"  [{action}]{status} {ad_name} | 목표:{target_rank}위 | {current_bid}→{new_bid}원 | {reason}")

        return changed


if __name__ == "__main__":
    # ── 네이버 API 클라이언트 ──────────────────────────────────────
    client = NaverAdClient(
        api_key=os.environ["NAVER_AD_API_KEY"],
        secret_key=os.environ["NAVER_AD_SECRET_KEY"],
        customer_id=os.environ["NAVER_AD_CUSTOMER_ID"],
    )

    # ── 구글 시트 연동 ─────────────────────────────────────────────
    # 깃헙 Secrets에서 JSON 내용을 환경변수로 받아서 임시 파일로 저장
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(sa_json)
        sa_path = f.name

    sheets = SheetsConfig(
        spreadsheet_id="1lw_vkuPx6yHePhOxgE1uI_0R7ft43x_I1XMXXfwVWbs",
        sheet_name="시트1",
        credentials_path=sa_path,
    )

    # ── 공통 설정 ──────────────────────────────────────────────────
    base_config = BidConfig(
        max_cpc=0,           # 더미 — 실제 값은 시트에서 행마다 읽음
        min_bid=70,
        step=10,
        safety_margin=0.95,
        adjust_ratio=0.10,
    )

    bidder = ShoppingBidder(client=client, base_config=base_config, sheets=sheets)

    # DRY_RUN 환경변수로 제어 (기본값 False)
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    bidder.run(dry_run=dry_run)
