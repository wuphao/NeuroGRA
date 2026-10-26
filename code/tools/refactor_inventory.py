"""Read-only source dependency inventory for the staged system refactor."""

import ast
import json
from pathlib import Path


def inventory(root):
    rows = []
    for path in sorted((root / "code").rglob("*.py")):
        if "__pycache__" in path.parts or ".egg-info" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        imports, references = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add("." * node.level + (node.module or ""))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if len(value) < 200 and (value.endswith((".yaml", ".json", ".py"))
                                        or value.startswith("neurogra.")):
                    references.add(value)
        rows.append({"path": path.relative_to(root).as_posix(),
                     "imports": sorted(imports), "literal_references": sorted(references)})
    return rows


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    destination = root / "output/system_refactor/dependency_inventory.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows = inventory(root)
    destination.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Inventoried {len(rows)} source files; dynamic references require caller inspection.")
