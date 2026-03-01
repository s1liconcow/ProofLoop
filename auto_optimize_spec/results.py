from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class AgentExecutionResult:
    runtime_type: str
    command: str
    summary: str
    input_tokens: int
    output_tokens: int
    exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    stdout_log_path: Optional[str] = None
    stderr_log_path: Optional[str] = None


@dataclass
class VerificationResult:
    passed: bool
    metrics: Dict[str, Any]
    command_exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float


@dataclass
class AttemptResult:
    attempt_index: int
    agent_id: str
    provider_id: Optional[str]
    model: Optional[str]
    draft_summary: str
    agent_runtime: AgentExecutionResult
    verification: VerificationResult
    score: float
    score_breakdown: Dict[str, float]
    agent_cost_usd: float
    workspace: str
