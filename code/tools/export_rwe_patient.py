#!/usr/bin/env python3
"""Export nine RWE forms and preprocess them with reusable patient-view rules.

The database is used only for read-only patient-number resolution. Form data
is fetched through the RWE API. This script never writes to RWE or PostgreSQL.

Adapted from Multi-agent/src/multi_agent/ingestion/rwe_export.py (local source).
Source SHA-256: 59bb53747939b95a7a33a62e427aa88a1b64938250b2e40d3ff3ad13851a3247.
Run from this checkout with: python code/tools/export_rwe_patient.py --patient-number <number>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neurogra.clinical.rwe_preprocessing import is_note_field, trim_patient


DEFAULT_PROJECT_ID = 8
DEFAULT_API_BASE_URL = "http://localhost:8081"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def local_settings(path: Path | None = None) -> dict[str, str]:
    path = path or PROJECT_ROOT / ".env.rwe.local"
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    values.update({k: v for k, v in os.environ.items() if k.startswith("RWE_")})
    return values


def export_arguments(patient_number: str, output: Path, settings: dict[str, str] | None = None,
                     *, trim_mode: str = 'short', detail_years=()) -> argparse.Namespace:
    settings = local_settings() if settings is None else settings
    return argparse.Namespace(
        patient_number=patient_number, output=output,
        project_id=int(settings.get("RWE_PROJECT_ID", "8")),
        api_base_url=settings.get('RWE_API_BASE_URL', DEFAULT_API_BASE_URL),
        login_account=settings.get('RWE_LOGIN_ACCOUNT', ''),
        login_password=settings.get('RWE_LOGIN_PASSWORD', ''),
        token="",
        db_host=settings.get("RWE_DB_HOST", "localhost"),
        db_port=int(settings.get("RWE_DB_PORT", "5432")),
        db_name=settings.get("RWE_DB_NAME", "rwe_nexus_develop"),
        db_user=settings.get("RWE_DB_USER", "postgres"),
        db_password=settings.get("RWE_DB_PASSWORD", ""), timeout=30.0,
        trim_mode=trim_mode, detail_years=list(detail_years),
    )


FORM_CONFIGS = (
    {"key": "moca", "id": 55, "name": "MOCA量表", "fill_mode": "multiple", "date_field": "访视日期", "group": "cognitive"},
    {"key": "mmse", "id": 56, "name": "MMSE量表", "fill_mode": "multiple", "date_field": "访视日期", "group": "cognitive"},
    {"key": "faq", "id": 57, "name": "FAQ量表", "fill_mode": "multiple", "date_field": "访视日期", "group": "functional"},
    {"key": "cdr", "id": 58, "name": "CDR量表", "fill_mode": "multiple", "date_field": "访视日期", "group": "functional"},
    {"key": "adas", "id": 59, "name": "ADAS量表", "fill_mode": "multiple", "date_field": "访视日期", "group": "cognitive"},
    {"key": "dicom_basic_info", "id": 87, "name": "DICOM基本信息", "fill_mode": "single", "date_field": None, "group": "demographics"},
    {"key": "plasma_biomarkers", "id": 88, "name": "血浆信息", "fill_mode": "multiple", "date_field": "血液采集日期", "group": "biomarker"},
    {"key": "apoe_genotype", "id": 89, "name": "基因风险信息", "fill_mode": "multiple", "date_field": "APOE检测日期", "group": "genetics"},
    {"key": "csf_biomarkers", "id": 90, "name": "脑脊液生物标志物", "fill_mode": "multiple", "date_field": "检查/采样日期", "group": "biomarker"},
)

SYSTEM_FIELDS = {
    "patient_id", "patientId", "parent_id", "parentId", "parent_field_id", "parentFieldId",
    "is_delete", "isDelete", "create_time", "createTime", "update_time", "updateTime", "comment",
}
# Confirmed by the source MoCA importer (ZERO_ONE_FIELDS and FFLUENCY).
# Match by form and field: scores, counts and lab values may also equal 0 or 1.
BINARY_FIELD_LABELS = {
    "moca": {
        **{field: ("错误", "正确") for field in (
            "时钟轮廓是否正确", "评估时钟数字", "评估指针",
            "看图说出狮子名称", "看图说出犀牛名称", "看图说出骆驼名称",
            "火车和自行车有什么共同点", "手表和尺子有什么共同点",
        )},
        "字母流畅性任务": ("未达到标准（<11 个词）", "达到标准（≥11 个词）"),
    },
}
CDR_OPTION_SCORES = {
    "正常": 0.0,
    "可疑或极轻度损害": 0.5,
    "轻度损害": 1.0,
    "中度损害": 2.0,
    "重度损害": 3.0,
}
CDR_DOMAIN_FIELDS = (
    "记忆能力", "时间、地点及人物定向能力", "判断和解决问题能力",
    "参与工作、购物、社交等社会事务的能力", "家庭事务", "个人护理",
)


class ExportFailure(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    settings = local_settings()
    parser = argparse.ArgumentParser(description="Export all nine RWE forms for one patient to JSON.")
    parser.add_argument("--patient-number", required=True, help="Exact patient_project.patient_number.")
    parser.add_argument("--project-id", type=int, default=int(settings.get("RWE_PROJECT_ID", DEFAULT_PROJECT_ID)))
    parser.add_argument("--output", type=Path, help="Output JSON path.")
    parser.add_argument("--api-base-url", default=settings.get('RWE_API_BASE_URL', DEFAULT_API_BASE_URL))
    parser.add_argument('--login-account', default=settings.get('RWE_LOGIN_ACCOUNT', ''))
    parser.set_defaults(login_password=settings.get('RWE_LOGIN_PASSWORD', ''))
    parser.add_argument("--db-host", default=settings.get("RWE_DB_HOST", "localhost"))
    parser.add_argument("--db-port", type=int, default=int(settings.get("RWE_DB_PORT", "5432")))
    parser.add_argument("--db-name", default=settings.get("RWE_DB_NAME", "rwe_nexus_develop"))
    parser.add_argument("--db-user", default=settings.get("RWE_DB_USER", "postgres"))
    parser.add_argument("--db-password", default=settings.get("RWE_DB_PASSWORD", ""))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--trim-mode", choices=('short', 'smoke', 'none'), default='short',
                        help="默认short：全部量表总分、最新完整分项；none：完整导出但去除备注列")
    parser.add_argument("--detail-years", type=int, nargs='+', default=[],
                        help="short模式额外保留完整分项的年份；默认无固定年份")
    args = parser.parse_args()
    if args.detail_years and args.trim_mode != 'short':
        parser.error('--detail-years仅适用于short模式')
    if any(not 1 <= year <= 9999 for year in args.detail_years):
        parser.error('--detail-years必须是1至9999的年份')
    return args


def dependencies() -> tuple[Any, Any]:
    missing: list[str] = []
    try:
        import psycopg2
    except ImportError:
        psycopg2 = None
        missing.append("psycopg2-binary")
    try:
        import requests
    except ImportError:
        requests = None
        missing.append("requests")
    if missing:
        raise ExportFailure("缺少依赖，请安装: " + " ".join(missing))
    return psycopg2, requests


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def resolve_patient(args: argparse.Namespace, psycopg2: Any) -> dict[str, Any]:
    sql = """
        SELECT pp.patient_id, TRIM(pp.patient_number)
        FROM patient_project pp
        JOIN patient p ON p.id = pp.patient_id
        WHERE pp.project_id = %s
          AND pp.is_delete = 0
          AND p.is_delete = 0
          AND TRIM(pp.patient_number) = %s
    """
    connection = psycopg2.connect(
        host=args.db_host, port=args.db_port, dbname=args.db_name,
        user=args.db_user, password=args.db_password, connect_timeout=10,
    )
    try:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(sql, (args.project_id, args.patient_number.strip()))
            rows = cursor.fetchall()
    finally:
        connection.close()
    if not rows:
        raise ExportFailure(
            f"项目 {args.project_id} 中找不到患者编号 {args.patient_number!r}。"
        )
    if len(rows) > 1:
        raise ExportFailure(f"患者编号 {args.patient_number!r} 匹配到 {len(rows)} 条有效记录。")
    return {"patient_id": int(rows[0][0]), "patient_number": rows[0][1]}


def authorization(token: str) -> str:
    token = clean_text(token)
    if not token:
        raise ExportFailure("API Token 为空。")
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def login_token(requests: Any, args: argparse.Namespace) -> str:
    """Obtain a fresh token for every export; never persist or print it."""
    from neurogra.clinical.rwe_auth import obtain_token, AuthenticationFailure
    try:
        return obtain_token(requests, args.api_base_url,
            getattr(args, 'login_account', ''), getattr(args, 'login_password', ''), timeout=args.timeout)
    except AuthenticationFailure as exc:
        raise ExportFailure(str(exc)) from None


def api_query_form(requests: Any, args: argparse.Namespace, form_id: int, patient_id: int) -> list[dict[str, Any]]:
    path = "/form/queryData"
    url = f"{args.api_base_url.rstrip('/')}{path}"
    payload = {
        "formId": form_id,
        "page": 0,
        "pageSize": 0,
        "criteria": [{"enName": "patient_id", "inputValue": patient_id, "isFuzzy": False}],
    }
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Authorization": authorization(args.token), "Content-Type": "application/json"},
            timeout=args.timeout,
        )
        if response.status_code in (401, 403):
            raise ExportFailure("RWE 自动登录后仍无法读取表单，请检查账号表单权限或重新运行导出。")
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        raise ExportFailure(f"API 请求失败 formId={form_id}: {exc}") from exc
    except ValueError as exc:
        raise ExportFailure(f"API 返回非 JSON formId={form_id}") from exc
    if not isinstance(result, dict) or result.get("code") != 0:
        message = result.get("message", result) if isinstance(result, dict) else result
        raise ExportFailure(f"API 业务失败 formId={form_id}: {message}")
    rows = ((result.get("data") or {}).get("list") or [])
    if not isinstance(rows, list):
        raise ExportFailure(f"API 表单数据不是列表 formId={form_id}")
    for row in rows:
        if not isinstance(row, dict):
            raise ExportFailure(f"API 返回无效记录 formId={form_id}")
        source_patient = row.get("patient_id", row.get("patientId"))
        if source_patient is not None and str(source_patient) != str(patient_id):
            raise ExportFailure(f"API 返回其他患者记录 formId={form_id}，已停止导出。")
    return rows


def normalize_date(value: Any) -> str | None:
    if value is None or clean_text(value) == "":
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = clean_text(value)
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def clean_record(raw: dict[str, Any], date_field: str | None,
                 form_key: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    record_id = raw.get("id")
    if record_id is not None:
        result["record_id"] = record_id
    event_date = normalize_date(raw.get(date_field)) if date_field else None
    if date_field:
        result["visit_date"] = event_date
    for key, value in raw.items():
        if key == "id" or key == date_field or key in SYSTEM_FIELDS or is_note_field(key):
            continue
        cleaned = normalize_value(value)
        labels = BINARY_FIELD_LABELS.get(form_key, {}).get(key)
        if labels is not None:
            # Unknown codes and missing values remain visible, never guessed.
            if cleaned in (0, "0"):
                cleaned = labels[0]
            elif cleaned in (1, "1"):
                cleaned = labels[1]
        result[key] = cleaned
    return result


def sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda item: (
            item.get("visit_date") is None,
            item.get("visit_date") or "9999-12-31",
            int(item.get("record_id") or 0),
        ),
    )


def cdr_quality_warnings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for record in records:
        scores: list[float] = []
        complete = True
        for field in CDR_DOMAIN_FIELDS:
            score = CDR_OPTION_SCORES.get(clean_text(record.get(field)))
            if score is None:
                complete = False
                break
            scores.append(score)
        cdrsb = record.get("CDRSB总分")
        try:
            source_total = float(cdrsb) if cdrsb is not None else None
        except (TypeError, ValueError):
            source_total = None
        if complete and source_total is not None:
            calculated = sum(scores)
            if abs(calculated - source_total) > 1e-6:
                warnings.append({
                    "form": "CDR量表",
                    "record_id": record.get("record_id"),
                    "visit_date": record.get("visit_date"),
                    "message": f"CDRSB={source_total:g} 与六领域简单求和={calculated:g} 不一致，保留系统原值。",
                })
    return warnings


def build_demographics(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {}
    record = records[0]
    return {
        "年龄": record.get("年龄"),
        "身高_cm": record.get("身高"),
        "体重_kg": record.get("体重"),
        "出生日期": normalize_date(record.get("出生日期")),
        "性别": record.get("性别"),
        "患病情况": record.get("患病情况"),
    }


def build_timeline(forms: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for form_key, form_data in forms.items():
        if form_key == "dicom_basic_info":
            continue
        for record in form_data["records"]:
            visit_date = record.get("visit_date")
            if not visit_date:
                continue
            event = by_date.setdefault(visit_date, {"date": visit_date, "assessments": {}})
            assessment = {
                key: value for key, value in record.items()
                if key not in {"record_id", "visit_date"}
            }
            if record.get("record_id") is not None:
                assessment["record_id"] = record["record_id"]
            existing = event["assessments"].get(form_key)
            if existing is None:
                event["assessments"][form_key] = assessment
            elif isinstance(existing, list):
                existing.append(assessment)
            else:
                event["assessments"][form_key] = [existing, assessment]
    return [by_date[key] for key in sorted(by_date)]


def default_output_path(patient_number: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", patient_number).strip("._") or "patient"
    return PROJECT_ROOT / "output" / f"patient_{safe}_analysis.json"


def export_patient(args: argparse.Namespace, on_form=None) -> dict[str, Any]:
    psycopg2, requests = dependencies()
    args.token = login_token(requests, args)
    patient = resolve_patient(args, psycopg2)
    forms: dict[str, dict[str, Any]] = {}
    missing_forms: list[str] = []
    warnings: list[dict[str, Any]] = []
    record_counts: dict[str, int] = {}
    source_note_fields: list[dict[str, Any]] = []

    for config in FORM_CONFIGS:
        if on_form:
            on_form(config["name"])
        raw_records = api_query_form(requests, args, config["id"], patient["patient_id"])
        for row in raw_records:
            omitted = [key for key in row if is_note_field(key)]
            if omitted:
                source_note_fields.append({'form': config['key'], 'record_id': row.get('id'),
                                           'omitted_fields': omitted})
        records = sort_records([
            clean_record(record, config["date_field"], config["key"]) for record in raw_records
        ])
        forms[config["key"]] = {
            "form_id": config["id"],
            "form_name": config["name"],
            "fill_mode": config["fill_mode"],
            "group": config["group"],
            "source_date_field": config["date_field"],
            "record_count": len(records),
            "records": records,
        }
        record_counts[config["name"]] = len(records)
        if not records:
            missing_forms.append(config["name"])
        for record in records:
            if config["fill_mode"] == "multiple" and not record.get("visit_date"):
                warnings.append({
                    "form": config["name"],
                    "record_id": record.get("record_id"),
                    "message": "多次填写记录缺少或无法识别访视日期，未放入 timeline。",
                })

    warnings.extend(cdr_quality_warnings(forms["cdr"]["records"]))
    dicom_records = forms["dicom_basic_info"]["records"]
    if len(dicom_records) > 1:
        warnings.append({
            "form": "DICOM基本信息",
            "message": f"单次填写表单存在 {len(dicom_records)} 条记录，demographics 使用第一条。",
        })

    output = {
        "schema_version": "2.0",
        "exported_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "project": {"id": args.project_id},
        "patient": patient,
        "imaging": {
            "mri": {
                "path": "",
                "status": "path_pending_manual_input",
                "note": "请手动填写该患者MRI影像文件或目录的绝对路径。",
            },
            "pet": {
                "path": "",
                "status": "path_pending_manual_input",
                "note": "请手动填写该患者PET影像文件或目录的绝对路径。",
            },
        },
        "demographics": build_demographics(dicom_records),
        "forms": forms,
        "timeline": build_timeline(forms),
        "data_quality": {
            "warnings": warnings,
            "missing_forms": missing_forms,
            "record_counts": record_counts,
        },
    }
    output_path = args.output or default_output_path(patient["patient_number"])
    source_bytes = json.dumps(output, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
    output, manifest = trim_patient(output, getattr(args, 'trim_mode', 'short'),
        complete_detail_years=getattr(args, 'detail_years', ()))
    manifest.update({'schema_version': '1', 'source': 'rwe_api_cleaned_export',
        'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
        'source_hash_format': 'utf8_json_ensure_ascii_false_sort_keys_true',
        'source_note_columns_removed': source_note_fields,
        'patient_number': patient['patient_number'], 'exported_at': output['exported_at'],
        'output_file': str(output_path.resolve())})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding="utf-8")
    manifest['output_sha256'] = hashlib.sha256(output_path.read_bytes()).hexdigest()
    output_path.with_suffix('.manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return output


def main() -> int:
    args = parse_args()
    try:
        output = export_patient(args)
        print(json.dumps({"status": "success", "output": str(args.output or default_output_path(args.patient_number)), "records": sum(output["data_quality"]["record_counts"].values())}, ensure_ascii=False))
        return 0
    except ExportFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
