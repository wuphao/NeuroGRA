"""Read only single-file 3D volumes before launching the raw classifier."""
import argparse
import json
from pathlib import Path
import numpy as np
import SimpleITK as sitk
p = argparse.ArgumentParser()
p.add_argument('--mri', required=True)
p.add_argument('--pet', required=True)
p.add_argument('--output', required=True)
a = p.parse_args()
issues = []
for modality, path in [('MRI', a.mri), ('PET', a.pet)]:
    try:
        image = sitk.ReadImage(path)
        array = sitk.GetArrayFromImage(image)
        if image.GetDimension() != 3 or array.ndim != 3 or not array.size:
            issues.append(modality + ':not_3d_volume')
        elif not np.isfinite(array).all() or float(array.max()) <= float(array.min()):
            issues.append(modality + ':nonfinite_or_constant_volume')
        if any(not np.isfinite(v) or v <= 0 for v in image.GetSpacing()):
            issues.append(modality + ':invalid_spacing')
    except Exception as exc:
        issues.append(modality + ':' + type(exc).__name__)
Path(a.output).write_text(json.dumps({'eligible': not issues, 'issues': issues}), encoding='utf-8')
