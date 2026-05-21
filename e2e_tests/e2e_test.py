"""E2E テスト: 全テスト動画をアップロードし、試合分割とハイライト検出を検証する."""

import asyncio
import json
import shutil
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

import os

import httpx
import websockets

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8030")
WS_URL = os.environ.get("E2E_WS_URL", BASE_URL.replace("http", "ws") + "/ws/upload")
POLL_INTERVAL = 5
CHUNK_SIZE = 64 * 1024

RESULTS_DIR = Path(__file__).parent / "results"

TEST_CASES = [
    {
        "file": "data/yagura5m_normal_1-match.mp4",
        "expected_matches": 1,
        "expected_rules": ["5min"],
        "description": "ガチヤグラ 1試合（通常終了）",
    },
    {
        "file": "data/asari5m_normal_1-match.mp4",
        "expected_matches": 1,
        "expected_rules": ["5min"],
        "description": "ガチアサリ 1試合（通常終了）",
    },
    {
        "file": "data/area5m_knockout_normal_2-match.mp4",
        "expected_matches": 2,
        "expected_rules": ["5min", "5min"],
        "description": "ガチエリア 2試合（KO+通常混合）",
    },
    {
        "file": "data/hoko5m_normal_1-match.mkv",
        "expected_matches": 1,
        "expected_rules": ["5min"],
        "description": "ガチホコ 1試合（通常終了）",
    },
    {
        "file": "data/nawabari_multi_2-match.mp4",
        "expected_matches": 2,
        "expected_rules": ["3min", "3min"],
        "description": "ナワバリ 2試合",
    },
    {
        "file": "data/5m_3m_5m_3-match.mp4",
        "expected_matches": 3,
        "expected_rules": ["5min", "3min", "5min"],
        "description": "混合（ガチ+ナワバリ） 3試合",
    },
]


async def upload_video(file_path: Path) -> str:
    file_size = file_path.stat().st_size
    filename = file_path.name

    print(f"  アップロード: {filename} ({file_size / 1024 / 1024:.1f} MB)")

    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "type": "start",
            "filename": filename,
            "size": file_size,
        }))

        sent = 0
        with open(file_path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                await ws.send(chunk)
                sent += len(chunk)
                resp = json.loads(await ws.recv())
                if resp["type"] == "progress":
                    pct = resp["percent"]
                    print(f"    upload: {pct}%", end="\r")

        await ws.send(json.dumps({"type": "upload_complete"}))
        result = json.loads(await ws.recv())

        if result["type"] != "job_created":
            raise RuntimeError(f"アップロード失敗: {result}")

        job_id = result["job_id"]
        print(f"    ジョブ作成: {job_id}")
        return job_id


async def poll_job(job_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            resp = await client.get(f"{BASE_URL}/jobs/{job_id}")
            data = resp.json()
            phase = data["phase"]

            parts = [f"phase={phase}"]
            if data.get("match_progress"):
                mp = data["match_progress"]
                parts.append(f"match {mp['current_match']}/{mp['total_matches']}")
            if data.get("analyzer_progress"):
                ap = data["analyzer_progress"]
                parts.append(f"frame {ap['frames_done']}/{ap['frames_total']}")
            print(f"    {' | '.join(parts)}", end="\r")

            if phase == "completed":
                print()
                return data
            elif phase == "failed":
                print()
                raise RuntimeError(f"ジョブ失敗: {data.get('error')}")

            await asyncio.sleep(POLL_INTERVAL)


async def download_and_extract(job_id: str, case_name: str) -> dict:
    case_dir = RESULTS_DIR / case_name
    case_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.get(f"{BASE_URL}/download/{job_id}")

    zip_bytes = BytesIO(resp.content)
    with zipfile.ZipFile(zip_bytes) as zf:
        zf.extractall(case_dir)
        print(f"    展開先: {case_dir}")
        print(f"    ZIP内容: {zf.namelist()}")

    matches_json_path = case_dir / "matches.json"
    if matches_json_path.exists():
        return json.loads(matches_json_path.read_text())
    return {}


def validate_case(case: dict, matches_data: dict, elapsed: float) -> dict:
    result = {
        "file": case["file"],
        "description": case["description"],
        "elapsed_seconds": round(elapsed, 1),
        "pass": True,
        "errors": [],
        "warnings": [],
    }

    matches = matches_data.get("matches", [])
    scan_readings = matches_data.get("scan_readings", [])

    actual_count = len(matches)
    expected_count = case["expected_matches"]
    if actual_count != expected_count:
        result["errors"].append(
            f"試合数: 期待={expected_count}, 実際={actual_count}"
        )
        result["pass"] = False

    expected_rules = case["expected_rules"]
    actual_rules = [m.get("duration_type") for m in matches]
    if actual_rules != expected_rules and actual_count == expected_count:
        result["errors"].append(
            f"ルール: 期待={expected_rules}, 実際={actual_rules}"
        )
        result["pass"] = False

    for i, m in enumerate(matches):
        if m.get("start_seconds", -1) < 0:
            result["errors"].append(f"match_{i+1}: start_seconds < 0")
            result["pass"] = False

        actual_type = m.get("duration_type")
        if i < len(expected_rules) and actual_type != expected_rules[i]:
            result["errors"].append(
                f"match_{i+1}: duration_type={actual_type}, 期待={expected_rules[i]}"
            )
            result["pass"] = False

    if not scan_readings:
        result["warnings"].append("scan_readingsが空")

    result["match_count"] = actual_count
    result["actual_rules"] = actual_rules
    result["scan_reading_count"] = len(scan_readings)
    result["matches_detail"] = matches

    return result


async def run_single_case(case: dict) -> dict:
    file_path = Path(__file__).parent / case["file"]
    if not file_path.exists():
        return {
            "file": case["file"],
            "description": case["description"],
            "pass": False,
            "errors": [f"ファイルが見つかりません: {file_path}"],
        }

    case_name = file_path.stem
    start = time.time()

    try:
        job_id = await upload_video(file_path)
        await poll_job(job_id)
        matches_data = await download_and_extract(job_id, case_name)
        elapsed = time.time() - start
        return validate_case(case, matches_data, elapsed)
    except Exception as e:
        elapsed = time.time() - start
        return {
            "file": case["file"],
            "description": case["description"],
            "elapsed_seconds": round(elapsed, 1),
            "pass": False,
            "errors": [f"{type(e).__name__}: {e}"],
        }


async def main() -> None:
    if len(sys.argv) > 1:
        target = sys.argv[1]
        cases = [c for c in TEST_CASES if target in c["file"]]
        if not cases:
            print(f"該当するテストケースがありません: {target}")
            sys.exit(1)
    else:
        cases = TEST_CASES

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for case in cases:
        case_name = Path(case["file"]).stem
        case_dir = RESULTS_DIR / case_name
        if case_dir.exists():
            shutil.rmtree(case_dir)
    total_start = time.time()
    results = []

    print(f"=== E2Eテスト開始: {len(cases)}件 ===\n")

    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['description']}")
        print(f"  ファイル: {case['file']}")
        print(f"  期待: {case['expected_matches']}試合 {case['expected_rules']}")

        result = await run_single_case(case)
        results.append(result)

        status = "PASS" if result["pass"] else "FAIL"
        elapsed_str = f"{result.get('elapsed_seconds', 0):.0f}s"
        print(f"  結果: {status} ({elapsed_str})")
        if result.get("errors"):
            for err in result["errors"]:
                print(f"    ERROR: {err}")
        if result.get("warnings"):
            for warn in result["warnings"]:
                print(f"    WARN: {warn}")
        print()

    total_elapsed = time.time() - total_start
    passed = sum(1 for r in results if r["pass"])
    failed = len(results) - passed

    print("=" * 50)
    print(f"結果: {passed}/{len(results)} PASS, {failed} FAIL")
    print(f"総所要時間: {total_elapsed:.0f}秒")
    print("=" * 50)

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_elapsed_seconds": round(total_elapsed, 1),
        "summary": {"total": len(results), "passed": passed, "failed": failed},
        "cases": results,
    }
    report_path = RESULTS_DIR / "e2e_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nレポート: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
