"""Lossless JSON intake; only explicit attachment keys trigger file access."""
import hashlib
import json
from pathlib import Path
from .config import ClinicalConfig
from .schemas import (Attachment, ImageAsset, IntakeResult, Issue, PatientInput, SourceRecord,
                      is_rwe_patient_json)
from .utils import dumps, fingerprint, identity, parse_time

PATH_KEYS = {"路径", "报告路径", "文件路径", "附件路径"}
CONTEXT_KEYS = {"时间", "日期", "检查日期", "评估时间", "单位", "参考范围", "方法",
                "样本", "名称", "表格名称", "量表名称", "版本", "模态", "序列", "示踪剂", "教育校正", "语言", "检测平台", "来源"}


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
    patient = PatientInput.model_validate(raw)
    json.dumps(raw, allow_nan=False)
    result = IntakeResult(patient_id=patient.patient_id, raw_snapshot=raw, raw_input_hash=fingerprint(raw))
    rwe_export = is_rwe_patient_json(raw)
    metadata_pointers = set()

    def escape(key):
        return key.replace('~', '~0').replace('/', '~1')

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
                if key == '来源' and isinstance(item, dict) and item.get('type') == 'rwe':
                    continue  # Provenance lives in context, not in clinical observations.
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
            source = context.get('rwe_source', context.get('来源'))
            if isinstance(source, dict) and source.get('type') == 'rwe' and '/原始记录/' in pointer:
                suffix = pointer.split('/原始记录/', 1)[1]
                record.locator['rwe'] = {**source, 'value_pointer': source['row_pointer'] + '/' + suffix}
            elif isinstance(source, dict) and source.get('type') == 'rwe_export':
                record.locator['rwe'] = {**source, 'value_pointer': pointer}
            if pointer in metadata_pointers:
                record.parse_status = 'source_metadata'
            image_path_field = rwe_export and image_scope and key == 'path'
            if key in PATH_KEYS or image_path_field:
                record.parse_status = "path_metadata"
                if isinstance(value, str):
                    resolved, status = ((None, 'path_pending_manual_input') if image_path_field and not value.strip()
                                        else resolve_asset(value, config))
                    # Raw images are inspected separately, not parsed as text.
                    if not (image_scope and key in {"路径", "path"}):
                        attachment = Attachment(attachment_id=identity("attachment", [result.raw_input_hash, pointer]),
                                                record_id=record.record_id, original_path=value,
                                                resolved_path=resolved, status=status, context=context)
                        result.attachments.append(attachment)
                    record.path = resolved
                    if status != "available":
                        result.issues.append(Issue(code="path_" + status, stage="intake",
                                                   message="附件路径不可用", affected_refs=[record.record_id]))
            result.records.append(record)
    if rwe_export:
        export_id = identity('json_export', result.raw_input_hash)
        result.patient_metadata = {
            'format': 'rwe_export', 'schema_version': raw.get('schema_version'),
            'export_id': export_id,
            'patient': raw['patient'], 'project': raw.get('project'),
            'exported_at': raw.get('exported_at'), 'data_quality': raw.get('data_quality'),
            'forms': {key: {name: value for name, value in form.items() if name != 'records'}
                      for key, form in raw['forms'].items()},
        }
        for form_key, form in raw['forms'].items():
            for index, row in enumerate(form['records']):
                pointer = f'/forms/{escape(form_key)}/records/{index}'
                metadata_pointers.update(pointer + '/' + key for key in
                                         ('record_id', 'visit_date', 'patient_id', 'patientId'))
                context = {'名称': form.get('form_name', form_key),
                           '表格名称': form.get('form_name', form_key), '时间': row.get('visit_date'),
                           '来源': {'type': 'rwe_export', 'project_id': (raw.get('project') or {}).get('id'),
                                    'export_id': export_id,
                                    'patient_number': patient.patient_id,
                                    'form_key': form_key, 'form_id': form.get('form_id'),
                                    'record_id': row.get('record_id'), 'row_pointer': pointer},
                           'rwe_form': {key: value for key, value in form.items() if key != 'records'}}
                # A clinical column named 来源 must not replace generated provenance.
                context['rwe_source'] = dict(context['来源'])
                visit(row, pointer, context)

        # These fields are a view of the first DICOM row. Preserve novel fields,
        # while the complete original document remains in raw_snapshot.
        demographics = raw.get('demographics')
        dicom_rows = raw['forms'].get('dicom_basic_info', {}).get('records', [])
        aliases = {'年龄': '年龄', '身高_cm': '身高', '体重_kg': '体重',
                   '出生日期': '出生日期', '性别': '性别', '患病情况': '患病情况'}
        if isinstance(demographics, dict):
            for key, value in demographics.items():
                if dicom_rows and aliases.get(key, key) in dicom_rows[0]:
                    continue
                visit(value, '/demographics/' + escape(key))

        for image_key, image in (raw.get('imaging') or {}).items():
            if not isinstance(image, dict):
                raise ValueError('RWE imaging entries must be objects')
            pointer = '/imaging/' + escape(image_key)
            modality = str(image.get('modality') or image_key).upper()
            image_path = image.get('path')
            if isinstance(image_path, str):
                resolved, status = ((None, 'path_pending_manual_input') if not image_path.strip()
                                    else resolve_asset(image_path, config))
            else:
                resolved, status = None, 'invalid'
            image_time = image.get('visit_date', image.get('date', image.get('time')))
            result.images.append(ImageAsset(
                asset_id=identity('image', [result.raw_input_hash, pointer]), modality=modality,
                path=resolved, status=status, time=parse_time(image_time),
                sequence=image.get('sequence'), tracer=image.get('tracer'),
                metadata={**image, 'original_path': image_path, 'input_pointer': pointer}))
            metadata_pointers.update(pointer + '/' + key for key in
                ('status', 'note', 'modality', 'visit_date', 'date', 'time', 'sequence', 'tracer'))
            visit(image, pointer, {'模态': modality, '时间': image_time}, image_scope=True)

        quality = raw.get('data_quality') or {}
        for warning in quality.get('warnings', []):
            result.issues.append(Issue(code='rwe_data_quality_warning', stage='intake',
                message=warning.get('message', dumps(warning)) if isinstance(warning, dict) else str(warning)))
        for name in quality.get('missing_forms', []):
            result.issues.append(Issue(code='rwe_form_empty', stage='intake',
                message=f'RWE 导出表单 {name} 没有记录，不能视为该患者未检查。'))
        known_keys = {'schema_version', 'exported_at', 'project', 'patient', 'forms',
                      'imaging', 'demographics', 'timeline', 'data_quality', '患者ID'}
        for key, value in raw.items():
            if key not in known_keys:
                visit(value, '/' + escape(key))
    else:
        visit(raw)
    for image in result.images:
        prefix = str(image.metadata["input_pointer"]) + "/"
        image.source_refs = [r.record_id for r in result.records
                             if str(r.locator.get("json_pointer", "")).startswith(prefix)]
    return result
