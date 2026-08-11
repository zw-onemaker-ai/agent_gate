"""Data models for AgentGate pipeline."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class GateStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    CONDITIONAL = "CONDITIONAL"


class LoopbackTarget(Enum):
    REQUIREMENTS = "R1"
    DESIGN = "R2"
    BACKEND = "R4"
    FRONTEND = "R5"
    SECURITY = "R6"
    NONE = "NONE"


class CTXBudget(Enum):
    """Context budget levels for assembled prompts."""
    NORMAL = "CTX_NORMAL"       # ≤8KB — full context
    WARNING = "CTX_WARNING"     # ≤16KB — contract + fail context
    CRITICAL = "CTX_CRITICAL"   # >16KB — summary only


@dataclass
class ExitCodeFingerprint:
    raw_output: str
    exit_code: int
    has_fingerprint: bool
    count: int = 0

    @classmethod
    def from_output(cls, output):
        import re
        matches = re.findall(r"EXIT:(\d+)", output)
        return cls(
            raw_output=output,
            exit_code=int(matches[-1]) if matches else -1,
            has_fingerprint=len(matches) > 0,
            count=len(matches),
        )


@dataclass
class Contract:
    """Agent产出契约——结构化Handoff（Phase 1）。
    由Agent自述产出内容，下游Agent消费此契约获取上游信息。
    """
    agent_id: str
    summary: str = ""                      # 一句话描述产出
    output_files: List[str] = field(default_factory=list)
    endpoints: List[dict] = field(default_factory=list)   # API端点
    start_command: str = ""                # 启动命令
    test_hints: List[str] = field(default_factory=list)   # 测试提示
    schema_info: dict = field(default_factory=dict)       # 数据格式/表结构


@dataclass
class AgentOutput:
    role: str
    role_name: str
    part_a_files: List[str] = field(default_factory=list)
    part_b: str = ""
    exit_fingerprint: Optional[ExitCodeFingerprint] = None
    quality_gate: GateStatus = GateStatus.FAIL
    context_card: str = ""
    contract: Optional[Contract] = None   # 🆕 Phase 1: structured handoff


@dataclass
class QualityGateResult:
    status: GateStatus
    checks: List[dict] = field(default_factory=list)
    exit_fingerprint: Optional[ExitCodeFingerprint] = None
    fail_reasons: List[str] = field(default_factory=list)
    loopback_target: LoopbackTarget = LoopbackTarget.NONE
    verified_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ContextPackage:
    package_id: str
    source_role: str
    target_roles: List[str]
    agent_output: AgentOutput
    gate_result: QualityGateResult
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class StageConfig:
    """Pipeline stage configuration (Q2 Phase 0)."""
    stage: int
    agents: List[str]
    mode: str = "serial"            # "serial" | "parallel"
    max_retries: int = 3            # 🆕 Q2: retry limit per stage
    on_fail: Optional[str] = None   # 🆕 Q2: loopback target on exhaustion


@dataclass
class DesignNotes:
    """Design brain metadata (Q2 Phase 0)."""
    why_these_agents: str = ""
    gaps_found: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)


@dataclass
class PipelineState:
    project_name: str
    steps: List[ContextPackage] = field(default_factory=list)
    loopback_count: int = 0
    current_role: str = "R1"
    max_iterations: int = 5
    design_notes: Optional[DesignNotes] = None  # 🆕 Q2
