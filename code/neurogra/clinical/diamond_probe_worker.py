"""Run only in DiaMond's interpreter; write aggregate validation facts, no patient identifiers."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--root', required=True)
p.add_argument('--checkpoint', required=True)
p.add_argument('--output', required=True)
a = p.parse_args()
report = {'environment_available': False, 'model_loaded': False, 'validation_status': 'blocked_validation', 'issues': []}
try:
    import torch
    import numpy as np
    import SimpleITK
    import monai
    import torchio
    report.update(environment_available=True, python=sys.version.split()[0], torch=torch.__version__,
                  cuda_available=torch.cuda.is_available(), simpleitk=SimpleITK.Version_VersionString(),
                  monai=monai.__version__, torchio=torchio.__version__)
    sys.path.insert(0, str(Path(a.root) / 'src'))
    spec = importlib.util.spec_from_file_location('raw_predict', Path(a.root) / 'tools/predict_diamond_raw.py')
    raw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(raw)
    checkpoint = torch.load(a.checkpoint, map_location='cpu', weights_only=False)
    report['checkpoint_keys'] = list(checkpoint)
    report['checkpoint_regbn_saved'] = any('regbn' in k.lower() for k in checkpoint)
    config = raw.load_hyperparams(Path(a.checkpoint))
    model, head, class_num, modality = raw.build_model(config, checkpoint, torch.device('cpu'))
    report.update(model_loaded=True, class_num=class_num, modality=modality)
    from adni import get_image_transform, DIAGNOSIS_MAP
    report['training_label_map'] = DIAGNOSIS_MAP
    sample = np.random.default_rng(7).random((24, 26, 28), dtype=np.float32)
    try:
        raw_tensor = raw.preprocess_volume(sample.copy())
        training_tensor = np.asarray(get_image_transform(False)(sample[np.newaxis].copy()))
        report['preprocessing_same_array_equal'] = bool(np.array_equal(raw_tensor.numpy(), training_tensor))
        report['raw_shape'] = list(raw_tensor.shape)
        report['training_shape'] = list(training_tensor.shape)
    except Exception as exc:
        report['issues'].append({'stage': 'preprocessing', 'error': type(exc).__name__, 'message': str(exc)[:500],
                                'cause': str(exc.__cause__)[:500]})
        training_tensor = np.asarray(get_image_transform(False)(sample[np.newaxis].copy()))
        report['training_preprocessing_shape'] = list(training_tensor.shape)
    try:
        reg = raw.RegBN(g_num_channels=192 if class_num == 3 else 128, f_layer_dim=[], g_layer_dim=[],
            normalize_input=True, normalize_output=True, affine=True, sigma_THR=0.0, sigma_MIN=0.0)
        report['raw_regbn_constructed'] = True
        report['regbn_state_keys'] = list(reg.state_dict())
    except Exception as exc:
        report['raw_regbn_constructed'] = False
        report['issues'].append({'stage': 'regbn_constructor', 'error': type(exc).__name__, 'message': str(exc)[:500]})
    if not report['checkpoint_regbn_saved']:
        report['issues'].append({'stage': 'checkpoint', 'message': '独立 RegBN 训练状态未保存在此 checkpoint，不能确认训练验证输出可重现。'})
    report['issues'].append({'stage': 'validation', 'message': '尚无与此权重绑定的训练参考输出一致性验收；未执行患者分类。'})
except Exception as exc:
    report['issues'].append({'stage': 'environment_or_model', 'error': type(exc).__name__, 'message': str(exc)[:500]})
Path(a.output).parent.mkdir(parents=True, exist_ok=True)
Path(a.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
