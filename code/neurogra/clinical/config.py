"""Clinical configuration is separate from patient data."""
from pathlib import Path
from typing import Literal
import yaml
from pydantic import Field
from .schemas import Contract, TaskBudget


class ModelConfig(Contract):
    provider: Literal["ollama"] = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "qwen3.6:35b"
    timeout_seconds: float = Field(default=120, gt=0)
    max_output_tokens: int = Field(default=4096, gt=0)
    context_tokens: int = Field(default=16384, gt=0)
    max_input_chars: int = Field(default=24000, gt=0)
    transport_attempts: int = Field(default=2, ge=1, le=3)


class BudgetConfig(Contract):
    llm: int = Field(default=48, ge=0)
    retrieval: int = Field(default=24, ge=0)
    image: int = Field(default=2, ge=0)
    seconds: float = Field(default=900, gt=0)


class VectorConfig(Contract):
    base_url: str = 'http://localhost:11434'
    model: str = 'bge-m3:latest'
    dimension: int = Field(default=1024, ge=1)
    max_chars: int = Field(default=1800, ge=100, le=8000)
    timeout_seconds: float = Field(default=45, gt=0, le=120)
    index_manifest: Path | None = None
    index_root: Path = Path('data/knowledge/vector_indexes')


class ClinicalConfig(Contract):
    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[3])
    patient_data_root: Path = Path("data/clinical/patients")
    allowed_roots: list[Path] = Field(default_factory=list)
    database: Path = Path("data/clinical/clinical.sqlite")
    data_root: Path = Path("data/clinical")
    output_root: Path = Path("output/clinical")
    knowledge_config: Path = Path("configs/knowledge.local.ollama_neo4j.example.yaml")
    knowledge_release_id: str | None = None
    model: ModelConfig = Field(default_factory=ModelConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    max_parallel_tasks: int = Field(default=4, ge=1, le=8)
    task_budget: TaskBudget = Field(default_factory=TaskBudget)
    verify_narrative: bool = False
    max_attachment_bytes: int = Field(default=50_000_000, gt=0)
    max_records: int = Field(default=1000, gt=0)
    max_segments: int = Field(default=2000, gt=0)
    diamond_root: Path = Path("D:/Python Project/DiaMond/DiaMond")
    diamond_python: Path = Path("D:/Python Project/DiaMond/DiaMond/.venv/Scripts/python.exe")
    diamond_checkpoint: Path = Path("D:/Python Project/DiaMond/DiaMond/models/DiaMond/mri+pet/DiaMond_multi_split4_bestval.pt")
    diamond_device: Literal['cpu', 'cuda'] = 'cpu'
    diamond_timeout_seconds: float = Field(default=120, gt=0, le=600)
    diamond_max_pair_days: int = Field(default=0, ge=0, le=365)
    diamond_validation_report: Path | None = None
    vector_enabled: bool = False
    vector: VectorConfig = Field(default_factory=VectorConfig)

    def resolve(self, path: Path) -> Path:
        return (path if path.is_absolute() else self.project_root / path).resolve()


def load_config(path: str | Path | None = None) -> ClinicalConfig:
    if path is None:
        return ClinicalConfig()
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("clinical config must be a mapping")
    raw.setdefault("project_root", str(path.parent.parent))
    return ClinicalConfig.model_validate(raw)
