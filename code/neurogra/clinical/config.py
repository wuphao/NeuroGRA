"""Clinical configuration is separate from patient data."""
from pathlib import Path
from typing import Literal
import yaml
from pydantic import Field
from .schemas import Contract, TaskBudget
from .schemas import ROLES, Role
from neurogra_kb.config import KBConfig
from neurogra_kg.config import KGConfig
from .context_models import ContextPolicy


class KnowledgeAccess(Contract):
    use_kb: bool = True
    use_kg: bool = True


class AgentConfig(KnowledgeAccess):
    enabled: bool = True
    task_budget: TaskBudget | None = None


class ModelConfig(Contract):
    provider: Literal["ollama"] = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "qwen3.8:27b"
    timeout_seconds: float = Field(default=120, gt=0)
    max_output_tokens: int = Field(default=4096, gt=0)
    context_tokens: int = Field(default=16384, gt=0)
    max_input_chars: int = Field(default=24000, gt=0)
    transport_attempts: int = Field(default=2, ge=1, le=3)
    context: ContextPolicy = Field(default_factory=ContextPolicy)


class BudgetConfig(Contract):
    llm: int = Field(default=48, ge=0)
    retrieval: int = Field(default=12, ge=0)
    image: int = Field(default=2, ge=0)
    patient_data: int = Field(default=12, ge=0)
    embedding: int = Field(default=128, ge=0)
    seconds: float = Field(default=900, gt=0)


class VectorConfig(Contract):
    base_url: str = 'http://localhost:11434'
    model: str = 'qwen3-embedding:0.6b'
    dimension: int = Field(default=1024, ge=1)
    max_chars: int = Field(default=1800, ge=100, le=8000)
    timeout_seconds: float = Field(default=45, gt=0, le=120)
    index_manifest: Path | None = None
    index_root: Path = Path('data/kg/releases')


class ClinicalConfig(Contract):
    main_agent: KnowledgeAccess = Field(default_factory=KnowledgeAccess)
    agents: dict[Role, AgentConfig] = Field(default_factory=lambda: {r: AgentConfig() for r in ROLES})
    kb: KBConfig | None = None
    kg: KGConfig | None = None
    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[3])
    patient_data_root: Path = Path("data/clinical/patients")
    allowed_roots: list[Path] = Field(default_factory=list)
    database: Path = Path("data/clinical/clinical.sqlite")
    data_root: Path = Path("data/clinical")
    output_root: Path = Path("output/clinical")
    report_root: Path = Path('data/clinical/reports')
    knowledge_config: Path = Path("configs/kg.local.yaml")
    knowledge_release_id: str | None = None
    text_knowledge_config: Path | None = Path("configs/kb.local.yaml")
    text_knowledge_release_id: str | None = None
    rwe_config: Path | None = None
    max_rounds: int = Field(default=3, ge=1, le=3)
    diagnosis_task_mode: Literal['follow_up', 'blinded_classification'] = 'follow_up'
    kg_representation: Literal['full', 'plain_triples'] = 'full'
    research_classification: bool = False
    diagnosis_context_tokens: int = Field(default=32768, ge=8192)
    diagnosis_max_output_tokens: int = Field(default=2400, ge=512)
    synthesis_reserved_calls: int = Field(default=6, ge=2, le=16)
    knowledge_model_calls: int = Field(default=10, ge=0)
    condition_model_calls: int = Field(default=2, ge=0, le=6)
    diagnosis_evidence_chars: int = Field(default=14000, ge=2000, le=30000)
    diagnosis_max_evidence: int = Field(default=8, ge=4, le=12)
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

    def agent(self, role: Role) -> AgentConfig:
        # An omitted role is disabled when an explicit agent map is supplied.
        return self.agents.get(role, AgentConfig(enabled=False, use_kb=False, use_kg=False))

    def enabled_roles(self) -> list[Role]:
        return [r for r in ROLES if self.agent(r).enabled]

    def knowledge_enabled(self, backend: str) -> bool:
        return getattr(self.main_agent, 'use_' + backend) or any(
            getattr(self.agent(r), 'use_' + backend) for r in self.enabled_roles())

    def knowledge_access(self, roles=()) -> KnowledgeAccess:
        # Main may retrieve for itself or on behalf of enabled specialists.
        return KnowledgeAccess(**{'use_' + b: getattr(self.main_agent, 'use_' + b) or any(
            self.agent(r).enabled and getattr(self.agent(r), 'use_' + b) for r in roles)
            for b in ('kb', 'kg')})


def load_config(path: str | Path | None = None) -> ClinicalConfig:
    from neurogra.platform.configuration import resolve_config
    if path is None:
        return resolve_config()
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("clinical config must be a mapping")
    return resolve_config(path, root=raw.get('project_root', path.parent.parent))
