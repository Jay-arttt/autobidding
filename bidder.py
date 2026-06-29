"""
네이버 쇼핑검색광고 자동입찰
깃헙 액션에서 1회 실행되는 구조
"""

import os
import logging
import time
import tempfile
from typing import Optional, Dict

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

    def get_current_bid(self, ad_id: str) -> Optional[int]:
        """
        GET /ncc/ads/{nccAdId}
        소재의 현재 실제 입찰가를 네이버 API에서 직접 읽어옴
        """
        try:
            resp = self.client.get(f"/ncc/ads/{ad_id}")
            ad_attr = resp.get("adAttr", {})
            bid = ad_attr.get("bidAmt")
            if bid is not None:
                return int(bid)
            # useGroupBidAmt=True면 그룹 입찰가 사용 중
            if ad_attr.get("useGroupBidAmt"):
                log.info(f"  ({ad_id}) 그룹 입찰가 사용 중 — adgroup bidAmt로 폴백")
                adgroup_id = resp.get("nccAdgroupId")
                if adgroup_id:
                    return self._get_adgroup_bid(adgroup_id)
        except Exception as e:
            log.warning(f"현재 입찰가 조회 실패 ({ad_id}): {e}")
        return None

    def _get_adgroup_bid(self, adgroup_id: str) -> Optional[int]:
        """그룹 입찰가 사용 중일 때 광고그룹 bidAmt 조회"""
        try:
            resp = self.client.get(f"/ncc/adgroups/{adgroup_id}")
            return int(resp.get("bidAmt", 70))
        except Exception as e:
            log.warning(f"그룹 입찰가 조회 실패 ({adgroup_id}): {e}")
        return None

    def get_estimate(self, ad_id: str) -> Optional[EstimateData]:
        """POST /estimate/average-position-bid/ad"""
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
        """PUT /ncc/ads/{nccAdId}?fields=adAttr"""
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

        # ★ 현재 입찰가를 네이버 API에서 직접 조회
        current_bid = self.get_current_bid(ad_id)
        if current_bid is None:
            log.warning(f"  현재 입찰가 조회 실패 — {ad_name} 스킵")
            self.sheets.write_error(cfg["row"], "현재 입찰가 조회 실패")
            return False

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

        # 시트에 현재입찰가 / 새입찰가 기록
        if not dry_run:
            self.sheets.write_result(cfg["row"], current_bid, new_bid)

        action = "변경" if changed else "유지"
        status = "✓" if success else "✗"
        log.info(
            f"  [{action}]{status} {ad_name} | "
            f"목표:{target_rank}위 | {current_bid}→{new_bid}원 | {reason}"
        )

        return changed


if __name__ == "__main__":
    client = NaverAdClient(
        api_key=os.environ["NAVER_AD_API_KEY"],
        secret_key=os.environ["NAVER_AD_SECRET_KEY"],
        customer_id=os.environ["NAVER_AD_CUSTOMER_ID"],
    )

    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(sa_json)
        sa_path = f.name

    sheets = SheetsConfig(
        spreadsheet_id="1lw_vkuPx6yHePhOxgE1uI_0R7ft43x_I1XMXXfwVWbs",
        sheet_name="시트1",
        credentials_path=sa_path,
    )

    base_config = BidConfig(
        max_cpc=0,
        min_bid=70,
        step=10,
        safety_margin=0.95,
        adjust_ratio=0.10,
    )

    bidder = ShoppingBidder(client=client, base_config=base_config, sheets=sheets)

    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    bidder.run(dry_run=dry_run)
