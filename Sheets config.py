"""
구글 스프레드시트 연동 모듈

현재 시트 구조 (헤더 1행):
  A(0): 소재명
  B(1): 소재ID (nccAdId)
  C(2): 목표순위
  D(3): 최대CPC

프로그램이 자동으로 추가 기록하는 컬럼:
  E(4): 현재입찰가
  F(5): 새입찰가
  G(6): 마지막업데이트
  H(7): 비고 (오류 발생 시)
"""

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from typing import List, Dict
import logging

log = logging.getLogger(__name__)

# ── 컬럼 인덱스 (0-based) ─────────────────────────────────────────
COL = {
    "ad_name":      0,   # 소재명
    "ad_id":        1,   # 소재ID
    "target_rank":  2,   # 목표순위
    "max_cpc":      3,   # 최대CPC
    # 아래는 프로그램이 자동 기록
    "current_bid":  4,   # 현재입찰가
    "new_bid":      5,   # 새입찰가
    "updated_at":   6,   # 마지막업데이트
    "note":         7,   # 비고
}
HEADER_ROW = 1  # 1행은 헤더, 데이터는 2행부터


class SheetsConfig:

    def __init__(self, spreadsheet_id: str, sheet_name: str, credentials_path: str):
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds  = Credentials.from_service_account_file(credentials_path, scopes=scopes)
        gc     = gspread.authorize(creds)
        self.sheet = gc.open_by_key(spreadsheet_id).worksheet(sheet_name)
        self._ensure_headers()

    def _ensure_headers(self):
        """E~H 헤더가 없으면 자동으로 추가"""
        row1 = self.sheet.row_values(1)
        headers_to_add = {
            COL["current_bid"] + 1: "현재입찰가",
            COL["new_bid"]     + 1: "새입찰가",
            COL["updated_at"]  + 1: "마지막업데이트",
            COL["note"]        + 1: "비고",
        }
        for col_num, label in headers_to_add.items():
            idx = col_num - 1
            if idx >= len(row1) or not row1[idx].strip():
                self.sheet.update_cell(1, col_num, label)

    def load_ad_configs(self) -> List[Dict]:
        """2행부터 소재 설정 전체 로드"""
        rows = self.sheet.get_all_values()
        configs = []

        for i, row in enumerate(rows[HEADER_ROW:], start=HEADER_ROW + 1):
            # 소재ID 없으면 스킵
            ad_id = _get(row, COL["ad_id"])
            if not ad_id:
                continue

            try:
                configs.append({
                    "row":         i,
                    "ad_name":     _get(row, COL["ad_name"]),
                    "ad_id":       ad_id,
                    "target_rank": int(_get(row, COL["target_rank"]) or 3),
                    "max_cpc":     int(_get(row, COL["max_cpc"])     or 5000),
                    # 현재입찰가: 시트에 기록된 값 있으면 사용, 없으면 70원(최솟값)
                    "current_bid": int(_get(row, COL["current_bid"]) or 70),
                })
            except (ValueError, IndexError) as e:
                log.warning(f"행 {i} 파싱 실패: {e}")

        log.info(f"시트에서 {len(configs)}개 소재 로드")
        return configs

    def write_result(self, row: int, current_bid: int, new_bid: int):
        """입찰 결과 기록 (E, F, G열)"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.sheet.update_cells([
            gspread.Cell(row, COL["current_bid"] + 1, current_bid),
            gspread.Cell(row, COL["new_bid"]     + 1, new_bid),
            gspread.Cell(row, COL["updated_at"]  + 1, now),
        ])

    def write_error(self, row: int, message: str):
        """오류 발생 시 H열에 기록"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.sheet.update_cell(row, COL["note"] + 1, f"[오류 {now}] {message}")


def _get(row: list, idx: int) -> str:
    try:
        return row[idx].strip()
    except IndexError:
        return ""
