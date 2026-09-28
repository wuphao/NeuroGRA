"""DiaMond isolated-process tool. Failed validation never produces a prediction."""
import csv
import hashlib
import json
import math
import os
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Literal
from pydantic import Field
from .schemas import Contract
from .utils import identity, write_json


class DiamondModelProfile(Contract):
    fingerprint: str
    hashes: dict[str, str]
    validation_status: Literal['validated', 'blocked_validation'] = 'blocked_validation'
    label_map: dict[str, str] = Field(default_factory=lambda: {'0': 'CN', '1': 'MCI', '2': 'AD'})
    warnings: list[str] = Field(default_factory=list)


class EligibilityResult(Contract):
    eligible: bool
    mri_asset_id: str | None = None
    pet_asset_id: str | None = None
    reasons: list[str] = Field(default_factory=list)


class DiamondResult(Contract):
    result_id: str
    patient_id: str
    case_version: int
    status: Literal['completed', 'ineligible', 'blocked_validation', 'failed', 'timeout']
    asset_ids: list[str] = Field(default_factory=list)
    model_fingerprint: str
    input_hashes: dict[str, str] = Field(default_factory=dict)
    input_binding: dict = Field(default_factory=dict)
    prediction: str | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    score_type: str = 'softmax_uncalibrated'
    validation_status: str = 'blocked_validation'
    warnings: list[str] = Field(default_factory=list)
    raw_output: str | None = None
    elapsed_seconds: float = 0
    cache_hit: bool = False


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def model_profile(config):
    root = config.resolve(config.diamond_root)
    checkpoint = config.resolve(config.diamond_checkpoint)
    files = [checkpoint, checkpoint.parent / '_hyperparams.yaml', root / 'tools/predict_diamond_raw.py',
             root / 'src/DiaMond.py', root / 'src/regbn.py', root / 'src/adni.py', root / 'src/train.py',
             config.resolve(config.diamond_python), Path(__file__), Path(__file__).with_name('diamond_input_worker.py')]
    site_packages = config.resolve(config.diamond_python).parent.parent / 'Lib' / 'site-packages'
    files.extend(sorted(site_packages.glob('*.dist-info/METADATA')))
    hashes = {str(p): file_hash(p) if p.is_file() else 'missing' for p in files}
    profile = DiamondModelProfile(fingerprint=identity('diamond', hashes), hashes=hashes,
        warnings=['需要训练参考输出、空间预处理与独立RegBN状态的一致性验收。'])
    if config.diamond_validation_report:
        report = json.loads(config.resolve(config.diamond_validation_report).read_text(encoding='utf-8-sig'))
        required = ('training_reference_match', 'preprocessing_verified', 'regbn_restored', 'label_map_verified', 'repeatability_verified')
        if (report.get('model_fingerprint') == profile.fingerprint and report.get('validation_status') == 'validated'
                and report.get('class_num') == 3 and report.get('label_map') == profile.label_map
                and all(report.get(k) is True for k in required) and 'missing' not in hashes.values()):
            profile.validation_status = 'validated'
            profile.warnings = []
    return profile


def check_diamond_eligibility(assets, config):
    mri = [a for a in assets if (a.modality or '').upper() in {'MRI', 'MR'}]
    pet = [a for a in assets if (a.modality or '').upper() in {'PET', 'PT'}]
    reasons = []
    if len(mri) != 1 or len(pet) != 1:
        return EligibilityResult(eligible=False, reasons=['需要唯一、明确选择的一对MRI/PET；不能猜测多访视配对或补缺失模态。'])
    mri, pet = mri[0], pet[0]
    if (mri.sequence or '').upper() not in {'T1', 'T1W', 'T1WI'}:
        reasons.append('MRI未明确为支持的T1序列')
    if (pet.tracer or '').upper() not in {'FDG', '18F-FDG', 'F18-FDG'}:
        reasons.append('PET未明确为FDG示踪剂')
    try:
        if mri.time.precision != 'day' or pet.time.precision != 'day':
            raise ValueError()
        if abs((date.fromisoformat(mri.time.value) - date.fromisoformat(pet.time.value)).days) > config.diamond_max_pair_days:
            reasons.append('检查日期超出配置的配对窗口')
    except (ValueError, TypeError):
        reasons.append('影像缺少精确检查日期')
    roots = [config.resolve(config.patient_data_root), *[config.resolve(p) for p in config.allowed_roots]]
    for asset in (mri, pet):
        path = Path(asset.path).resolve() if asset.path else None
        if not path or not path.exists() or not any(path.is_relative_to(root) for root in roots):
            reasons.append('影像路径不可访问或不在授权数据目录')
            continue
        # Do not let the upstream largest-series heuristic silently choose input.
        if path.is_dir():
            reasons.append('DICOM目录尚未转换为已明确序列的单体积文件，不允许默认选最大序列')
        elif not str(path).lower().endswith(('.nii', '.nii.gz', '.mha', '.nrrd')):
            reasons.append('仅支持单文件三维体积；不接受隐式外部像素文件或未选定DICOM序列')
        if asset.metadata.get('modality_conflict') or asset.metadata.get('inspection_truncated'):
            reasons.append('影像元数据冲突或盘点不完整')
        if asset.status in {'unreadable', 'missing', 'forbidden', 'invalid'}:
            reasons.append('影像读取状态不允许执行')
        size = asset.metadata.get('size')
        if size is not None and (len(size) != 3 or any(v <= 0 for v in size)):
            reasons.append('输入不是有效的三维体积')
    return EligibilityResult(eligible=not reasons, mri_asset_id=mri.asset_id, pet_asset_id=pet.asset_id, reasons=reasons)


def run_process(arguments, timeout, log_path, cwd=None):
    """No shell interpolation. Kill the process tree on timeout."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('wb') as log:
        proc = subprocess.Popen([str(v) for v in arguments], shell=False, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            start_new_session=os.name != 'nt')
        job = None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            job = kernel.CreateJobObjectW(None, None)
            if job and not kernel.AssignProcessToJobObject(job, int(proc._handle)):
                kernel.CloseHandle(job)
                job = None
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                if job:
                    kernel.TerminateJobObject(job, 1)
                else:
                    try:
                        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=3)
                    finally:
                        if proc.poll() is None:
                            proc.kill()
                        proc.wait(timeout=15)
            else:
                import signal
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=15)
            raise
        finally:
            if job:
                kernel.CloseHandle(job)


def normalize_csv(path, mri, pet):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError('invalid_row_count')
    row = rows[0]
    if Path(row['mri']).resolve() != Path(mri).resolve() or Path(row['pet']).resolve() != Path(pet).resolve():
        raise ValueError('wrong_input_binding')
    scores = [float(row[f'prob_{i}']) for i in range(3)]
    index = int(row['pred_idx'])
    labels = ['CN', 'MCI', 'AD']
    if (not all(math.isfinite(v) and 0 <= v <= 1 for v in scores) or abs(sum(scores) - 1) > 1e-4
            or index not in range(3) or scores[index] != max(scores) or row['pred_label'] != labels[index]):
        raise ValueError('invalid_scores_or_label')
    return labels[index], dict(zip(labels, scores))


class DiamondTool:
    def __init__(self, config, store, run_id):
        self.config, self.store, self.run_id = config, store, run_id
        self.profile = model_profile(config)

    def predict(self, snapshot, deadline=None):
        self.profile = model_profile(self.config)
        with self.store.workflow_lock('diamond-' + self.profile.fingerprint):
            return self._predict(snapshot, deadline)

    def binding(self, snapshot):
        return {'patient_id': snapshot.patient_id, 'case_id': snapshot.case_id, 'case_version': snapshot.version,
                'device': self.config.diamond_device, 'pair_days': self.config.diamond_max_pair_days,
                'assets': [{'asset_id': a.asset_id, 'path': str(Path(a.path).resolve()),
                            'sha256': file_hash(a.path), 'modality': a.modality,
                            'sequence': a.sequence, 'tracer': a.tracer, 'time': a.time.model_dump(mode='json')}
                           for a in sorted(snapshot.images, key=lambda a: a.asset_id)]}

    def assert_reusable(self, saved, snapshot):
        """Fail closed without replacing the immutable historical inference."""
        reasons = []
        try:
            current = model_profile(self.config)
            eligibility = check_diamond_eligibility(snapshot.images, self.config)
            if saved.status != 'completed':
                reasons.append('previous_inference_not_completed')
            if current.validation_status != 'validated' or saved.validation_status != 'validated':
                reasons.append('validation_not_current')
            if saved.model_fingerprint != current.fingerprint:
                reasons.append('model_changed')
            if not eligibility.eligible:
                reasons.append('input_ineligible')
            elif not saved.input_binding or saved.input_binding != self.binding(snapshot):
                reasons.append('input_or_configuration_changed')
        except (OSError, ValueError, TypeError):
            reasons.append('binding_unverifiable')
        if reasons:
            self.store.event(self.run_id, 'diamond_reuse_blocked',
                             {'result_id': saved.result_id, 'reasons': reasons, 'action': 'start_new_run'})
            raise ValueError('diamond_reuse_blocked:' + ','.join(reasons) + ';start_new_run')

    def _predict(self, snapshot, deadline=None):
        started = time.time()
        eligibility = check_diamond_eligibility(snapshot.images, self.config)
        result = DiamondResult(result_id=identity('diamond_result', [self.run_id, snapshot.case_id, snapshot.version]),
            patient_id=snapshot.patient_id, case_version=snapshot.version,
            status='ineligible' if not eligibility.eligible else 'blocked_validation',
            asset_ids=[a.asset_id for a in snapshot.images], model_fingerprint=self.profile.fingerprint,
            validation_status=self.profile.validation_status, warnings=eligibility.reasons + self.profile.warnings)
        saved = [r for r in self.store.objects(self.run_id, 'diamond_result') if r['object_id'] == result.result_id]
        if saved:
            previous = DiamondResult.model_validate(saved[-1]['payload'])
            self.assert_reusable(previous, snapshot)
            return previous
        if eligibility.eligible and self.profile.validation_status == 'validated':
            selected = {a.asset_id: a for a in snapshot.images}
            mri, pet = selected[eligibility.mri_asset_id], selected[eligibility.pet_asset_id]
            result.input_hashes = {a.asset_id: file_hash(a.path) for a in (mri, pet)}
            result.input_binding = self.binding(snapshot)
            key = identity('cache', [self.profile.fingerprint,
                [(a.path, result.input_hashes[a.asset_id]) for a in (mri, pet)], self.config.diamond_device])
            directory = self.config.resolve(self.config.output_root) / 'diamond_cache' / key
            csv_path = directory / 'prediction.csv'
            manifest_path = directory / 'manifest.json'
            call_id = None
            try:
                if manifest_path.exists() and csv_path.exists():
                    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                    if manifest.get('csv_sha256') == file_hash(csv_path) and manifest.get('cache_key') == key:
                        result.prediction, result.scores = normalize_csv(csv_path, mri.path, pet.path)
                        result.status, result.cache_hit, result.raw_output = 'completed', True, str(csv_path)
                if result.cache_hit:
                    self.assert_reusable(result, snapshot)
                    return self._save(result, started)
                call_id, remaining = self.store.reserve(self.run_id, 'image')
                command = [self.config.resolve(self.config.diamond_python), self.config.resolve(self.config.diamond_root) / 'tools/predict_diamond_raw.py',
                    '--mri', mri.path, '--pet', pet.path, '--checkpoint', self.config.resolve(self.config.diamond_checkpoint),
                    '--output-csv', csv_path, '--device', self.config.diamond_device]
                remaining = min(remaining, self.config.diamond_timeout_seconds,
                    deadline - time.time() if deadline is not None else remaining)
                if remaining <= 0:
                    raise TimeoutError('task_deadline')
                execution_deadline = time.time() + remaining
                check_path = directory / 'input_check.json'
                preflight = [self.config.resolve(self.config.diamond_python), Path(__file__).with_name('diamond_input_worker.py'),
                             '--mri', mri.path, '--pet', pet.path, '--output', check_path]
                code = run_process(preflight, remaining, directory / 'input_check.log')
                input_check = json.loads(check_path.read_text(encoding='utf-8')) if code == 0 else {'eligible': False}
                if not input_check.get('eligible'):
                    raise ValueError('volume_preflight_failed')
                remaining = execution_deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError('task_deadline')
                code = run_process(command, remaining, directory / 'process.log')
                if code:
                    raise RuntimeError('diamond_nonzero_exit')
                current = model_profile(self.config)
                if (current.fingerprint != self.profile.fingerprint or current.validation_status != 'validated' or
                        any(file_hash(a.path) != result.input_hashes[a.asset_id] for a in (mri, pet))):
                    raise ValueError('input_or_model_changed_during_execution')
                result.prediction, result.scores = normalize_csv(csv_path, mri.path, pet.path)
                result.raw_output = str(csv_path)
                result.status = 'completed'
                write_json(manifest_path, {'cache_key': key, 'csv_sha256': file_hash(csv_path)})
            except Exception as exc:
                result.status = 'timeout' if isinstance(exc, subprocess.TimeoutExpired) else 'failed'
                result.prediction, result.scores = None, {}
                result.warnings.append(type(exc).__name__)
            finally:
                if call_id:
                    self.store.settle(call_id, result.status)
        return self._save(result, started)

    def _save(self, result, started):
        result.elapsed_seconds = time.time() - started
        self.store.save(self.run_id, 'diamond_result', result.result_id, result)
        write_json(self.config.resolve(self.config.output_root) / self.run_id / (result.result_id + '.json'), result)
        self.store.event(self.run_id, 'diamond_finished', {'result_id': result.result_id, 'status': result.status})
        return result
