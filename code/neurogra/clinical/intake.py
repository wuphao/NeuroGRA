"""Lossless JSON intake; only explicit attachment keys trigger file access."""
import hashlib
import json
from pathlib import Path
from .config import ClinicalConfig
from .schemas import Attachment, ImageAsset, IntakeResult, Issue, PatientInput, SourceRecord
from .utils import dumps, fingerprint, identity, parse_time

PATH_KEYS = {"路径", "报告路径", "文件路径", "附件路径"}
CONTEXT_KEYS = {"时间", "日期", "检查日期", "评估时间", "单位", "参考范围", "方法",
                "样本", "名称", "表格名称", "量表名称", "版本", "模态", "序列", "示踪剂", "教育校正", "语言", "检测平台"}


def resolve_asset(path_text: str, config: ClinicalConfig) -> tuple[str | None, str]:
    try:
        path = Path(path_text)
        path = (path if path.is_absolute() else config.resolve(config.patient_data_root) / path).resolve()
        roots = [config.resolve(config.patient_data_root), *[config.resolve(p) for p in config.allowed_roots]]
        if not any(path.is_relative_to(root) for root in roots):
            return None, "forbidden"
        return str(path), "available" if path.exists() else "missing"
    except (ValueError, OSError):
        return None, "invalid"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ingest_patient(raw: dict, config: ClinicalConfig) -> IntakeResult:
    # Validate arbitrary values as JSON as well as patient ID; reject NaN.
    PatientInput.model_validate(raw)
    json.dumps(raw, allow_nan=False)
    result = IntakeResult(patient_id=raw["患者ID"], raw_snapshot=raw, raw_input_hash=fingerprint(raw))

    def visit(value, pointer="", context=None, image_scope=False):
        context = dict(context or {})
        if isinstance(value, dict):
            context.update({key: val for key, val in value.items() if key in CONTEXT_KEYS})
            is_image = ("模态" in value) or image_scope
            if is_image and "路径" in value:
                image_path = value.get("路径")
                resolved, status = resolve_asset(image_path, config) if isinstance(image_path, str) else (None, "invalid")
                result.images.append(ImageAsset(
                    asset_id=identity("image", [result.raw_input_hash, pointer]),
                    modality=str(value["模态"]) if value.get("模态") is not None else None,
                    time=parse_time(value.get("时间")), path=resolved,
                    sequence=value.get("序列"), tracer=value.get("示踪剂"), status=status,
                    metadata={"original_path": image_path, "input_pointer": pointer}))
            for key, item in value.items():
                escaped = key.replace("~", "~0").replace("/", "~1")
                visit(item, pointer + "/" + escaped, context,
                      is_image or key in {"影像数据", "影像资料"})
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, pointer + "/" + str(index), context, image_scope)
        else:
            if len(result.records) >= config.max_records:
                raise ValueError("max_records_exceeded: split the patient input explicitly")
            key = pointer.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
            record = SourceRecord(
                record_id=identity("record", [result.raw_input_hash, pointer]),
                kind="field", locator={"json_pointer": pointer}, raw_value=value,
                text=value if isinstance(value, str) else dumps(value), context=context,
                content_hash=fingerprint(value))
            if key in PATH_KEYS:
                record.parse_status = "path_metadata"
                if isinstance(value, str):
                    resolved, status = resolve_asset(value, config)
                    attachment = Attachment(attachment_id=identity("attachment", [result.raw_input_hash, pointer]),
                                            record_id=record.record_id, original_path=value,
                                            resolved_path=resolved, status=status, context=context)
                    # Raw images are inspected separately, not parsed as text.
                    if not (image_scope and key == "路径"):
                        result.attachments.append(attachment)
                    record.path = resolved
                    if status != "available":
                        result.issues.append(Issue(code="path_" + status, stage="intake",
                                                   message="附件路径不可用", affected_refs=[record.record_id]))
            result.records.append(record)
    visit(raw)
    for image in result.images:
        prefix = str(image.metadata["input_pointer"]) + "/"
        image.source_refs = [r.record_id for r in result.records
                             if str(r.locator.get("json_pointer", "")).startswith(prefix)]
    return result
