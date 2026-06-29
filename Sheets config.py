"""
구글 스프레드시트 연동 모듈

스프레드시트 구조 (헤더 1행 고정):
┌──────────────┬──────────────┬──────────┬──────────┬────────────┬──────────┬──────────┬─────────────────┬────────────┐
│ nccAdId      │ 소재명(메모) │집중키워드│목표순위  │ 최대CPC(원)│현재입찰가│새입찰가  │ 마지막업데이트  │ 비고       │
├──────────────┼──────────────┼──────────┼──────────┼────────────┼──────────┼──────────┼─────────────────┼────────────┤
│nad-xxx-001   │강아지덴탈껌  │강아지덴탈│ 1        │ 5000       │ 300      │(자동입력)│(자동입력)       │            │
│nad-xxx-002   │코코넛껌      │코코넛껌  │ 2        │ 3000       │ 300      │(자동입력)│(자동입력)       │            │
└──────────────┴──────────────┴──────────┴──────────┴────────────┴──────────┴──────────┴─────────────────┴────────────┘

컬럼 인덱스 (0-based):
  0: nccAdId
  1: 소재명 (메모용)
  2: 집중키워드 (메모용 — 실제 계산은 estimate API 사용)
  3: 목표순위
  4: 최대CPC
  5: 현재입찰가  ← 프로그램이 읽고 씀
  6: 새입찰가    ← 프로그램이 씀
  7: 마지막업데이트 ← 프로그램이 씀
  8: 비고        ← 사람이 씀
"""

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from typing import List, Dict, Optional
import logging

log = logging.getLogger(__name__)

# 컬럼 인덱스
COL = {
    "ad_id":        0,
    "ad_name":      1,
    "keyword":      2,
    "target_rank":  3,
    "max_cpc":      4,
    "current_bid":  5,
    "new_bid":      6,
    "updated_at":   7,
    "note":         8,
}
HEADER_ROW = 1  # 1행은 헤더


class SheetsConfig:
    """구글 스프레드시트에서 소재 설정을 읽고 결과를 기록"""

    def __init__(self, spreadsheet_id: str, sheet_name: str, credentials_path: str):
        """
        Args:
            spreadsheet_id:   스프레드시트 URL의 /d/{ID}/ 부분
            sheet_name:       시트 탭 이름 (예: "자동입찰설정")
            credentials_path: 서비스 계정 JSON 키 파일 경로
        """
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        creds = Credentials.from_service_account_file(
            credentials_path, scopes=scopes
        )
        gc = gspread.authorize(creds)
        self.sheet = gc.open_by_key(spreadsheet_id).worksheet(sheet_name)

    def load_ad_configs(self) -> List[Dict]:
        """
        스프레드시트에서 소재 설정 전체 로드
        Returns: [{"ad_id": ..., "target_rank": ..., "max_cpc": ...}, ...]
        """
        rows = self.sheet.get_all_values()
        configs = []

        for i, row in enumerate(rows[HEADER_ROW:], start=HEADER_ROW + 1):
            # 빈 행 또는 ad_id 없으면 스킵
            if not row or not row[COL["ad_id"]].strip():
                continue

            try:
                configs.append({
                    "row":          i,                             # 시트 행 번호 (1-based)
                    "ad_id":        row[COL["ad_id"]].strip(),
                    "ad_name":      _safe_get(row, COL["ad_name"]),
                    "keyword":      _safe_get(row, COL["keyword"]),
                    "target_rank":  int(_safe_get(row, COL["target_rank"]) or 3),
                    "max_cpc":      int(_safe_get(row, COL["max_cpc"]) or 5000),
                    "current_bid":  int(_safe_get(row, COL["current_bid"]) or 70),
                })
            except (ValueError, IndexError) as e:
                log.warning(f"  행 {i} 파싱 실패: {e} | row={row}")

        log.info(f"스프레드시트에서 {len(configs)}개 소재 로드")
        return configs

    def write_result(self, row: int, current_bid: int, new_bid: int, reason: str = ""):
        """
        입찰 결과를 스프레드시트에 기록
        현재입찰가 / 새입찰가 / 마지막업데이트 컬럼 업데이트
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        # gspread는 1-based 행, 1-based 컬 사용
        updates = [
            gspread.Cell(row, COL["current_bid"] + 1, current_bid),
            gspread.Cell(row, COL["new_bid"] + 1,     new_bid),
            gspread.Cell(row, COL["updated_at"] + 1,  now),
        ]
        self.sheet.update_cells(updates)

    def write_error(self, row: int, message: str):
        """오류 발생 시 비고 컬럼에 기록"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.sheet.update_cell(row, COL["note"] + 1, f"[오류 {now}] {message}")


def _safe_get(row: list, idx: int, default: str = "") -> str:
    try:
        return row[idx].strip()
    except IndexError:
        return default
