from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class FileRef(BaseModel):
    path: str
    sha256: Optional[str] = None


class ArtifactRef(BaseModel):
    name: str
    path: str
    mount_to: str
    read_only: bool = True
    type: Literal["dataset", "codebase", "binary", "config", "other"] = "other"


class PromptTemplate(BaseModel):
    system: str
    developer: Optional[str] = None
    user_prefix: Optional[str] = None
    variables: Dict[str, Union[str, int, float, bool]] = Field(default_factory=dict)


class EnvironmentInput(BaseModel):
    dockerfile: Optional[FileRef] = None
    base_image: Optional[str] = None
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    setup_commands: List[str] = Field(default_factory=list)


class BaselineSpec(BaseModel):
    description: Optional[str] = None
    metrics: Dict[str, float] = Field(default_factory=dict)


class InputContract(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    format: Literal["stdin", "files", "http", "simulator", "custom"]
    input_schema: Optional[Dict[str, Any]] = Field(default=None, alias="schema")
    examples: List[Dict[str, Any]] = Field(default_factory=list)


class OutputContract(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    format: Literal["patch", "command", "file_bundle", "text", "json", "custom"]
    required_paths: List[str] = Field(default_factory=list)
    output_schema: Optional[Dict[str, Any]] = Field(default=None, alias="schema")


class MetricSpec(BaseModel):
    name: str
    source: Literal["verifier", "runner", "provider", "agent", "custom"]
    type: Literal["number", "boolean", "string"]
    unit: Optional[str] = None
    required: bool = True


class VerificationSimulatorSpec(BaseModel):
    entrypoint: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)


class VerificationSpec(BaseModel):
    type: Literal["command", "test_suite", "script", "simulator", "api_call", "custom"]
    pass_condition: Optional[str] = None
    collect_metrics: List[MetricSpec] = Field(default_factory=list)
    timeout_seconds: int = 600
    retries: int = 0
    command: Optional[str] = None
    script: Optional[FileRef] = None
    simulator: Optional[VerificationSimulatorSpec] = None


class ScoringSpec(BaseModel):
    mode: Literal["maximize", "minimize", "composite"] = "composite"
    builtins: List[Literal["runtime", "compute", "agent_cost", "pass_rate", "memory", "energy"]] = Field(
        default_factory=list
    )
    formula: Optional[str] = None
    weights: Dict[str, float] = Field(default_factory=dict)
    tie_breakers: List[str] = Field(default_factory=list)


class ProblemSpec(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    domain: Literal["software-performance", "legacy-porting", "algorithms", "robotics", "ml", "other"] = "other"
    goal: str
    baseline: Optional[BaselineSpec] = None
    constraints: List[str] = Field(default_factory=list)
    input_contract: InputContract
    output_contract: OutputContract
    verification: VerificationSpec
    scoring: Optional[ScoringSpec] = None
    prompt_template: Optional[PromptTemplate] = None
    time_budget_seconds: Optional[int] = None
    max_attempts_per_agent: int = 4


class ProblemSetSpec(BaseModel):
    id: str
    selection: Literal["all", "sample", "top_k"]
    sample_size: Optional[int] = None
    top_k: Optional[int] = None
    aggregate_scoring: Optional[ScoringSpec] = None
    problems: List[ProblemSpec]


class JobSpec(BaseModel):
    id: str
    name: str
    target: Union[ProblemSpec, ProblemSetSpec]
    environment: Optional[EnvironmentInput] = None
    global_prompt_template: Optional[PromptTemplate] = None


class ProviderModelSpec(BaseModel):
    name: str
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


class ProviderPricingSpec(BaseModel):
    input_token_usd_per_million: Optional[float] = None
    output_token_usd_per_million: Optional[float] = None


class ProviderSpec(BaseModel):
    id: str
    type: Literal["openai", "anthropic", "google", "azure_openai", "bedrock", "ollama", "custom"]
    endpoint: Optional[str] = None
    api_key_env: Optional[str] = None
    models: List[ProviderModelSpec]
    pricing: Optional[ProviderPricingSpec] = None


class AgentSpec(BaseModel):
    class AgentRuntimeSpec(BaseModel):
        type: Literal["internal_mock", "claude_code", "codex", "opencode"] = "internal_mock"
        executable: Optional[str] = None
        args: List[str] = Field(default_factory=list)
        prompt_mode: Literal["stdin", "arg"] = "stdin"
        env: Dict[str, str] = Field(default_factory=dict)
        timeout_seconds: int = 900
        prompt_template: Optional[str] = None

    id: str
    persona: str
    provider_id: Optional[str] = None
    model: Optional[str] = None
    runtime: Optional[AgentRuntimeSpec] = None
    strategy: Literal["explore", "exploit", "balanced", "adversarial", "custom"] = "balanced"
    max_iterations: int = 4
    tooling: List[Literal["shell", "python", "git", "web", "simulator", "custom"]] = Field(default_factory=list)
    prompt_overrides: Optional[PromptTemplate] = None


class RunnerResourceLimits(BaseModel):
    cpu: Optional[str] = None
    memory: Optional[str] = None
    gpu: Optional[str] = None
    timeout_seconds: Optional[int] = None


class RunnerCache(BaseModel):
    enabled: bool = False
    mount_path: Optional[str] = None


class RunnerSpec(BaseModel):
    type: Literal["docker", "kubernetes", "local", "remote", "custom"]
    ephemeral: bool = True
    parallelism: int = 4
    image: Optional[str] = None
    resource_limits: Optional[RunnerResourceLimits] = None
    network_policy: Literal["disabled", "allowlist", "full"] = "allowlist"
    cache: Optional[RunnerCache] = None


class OrchestratorSpec(BaseModel):
    selection_policy: Literal["best_score", "first_pass", "pareto"] = "best_score"
    retry_policy: Literal["on_fail", "on_low_score", "never"] = "on_fail"
    max_total_attempts: int = 100
    feedback_mode: Literal["full_verifier_output", "summary_only", "redacted"] = "summary_only"


class ReportingSpec(BaseModel):
    output_dir: Optional[str] = None
    emit: List[Literal["json", "html", "junit", "csv", "trace"]] = Field(default_factory=list)
    store_agent_transcripts: bool = True


class OptimizationJob(BaseModel):
    schema_version: str = "v1"
    job: JobSpec
    agents: List[AgentSpec]
    providers: List[ProviderSpec] = Field(default_factory=list)
    runner: RunnerSpec
    orchestrator: Optional[OrchestratorSpec] = None
    reporting: Optional[ReportingSpec] = None
    metadata: Dict[str, Union[str, int, float, bool, None]] = Field(default_factory=dict)
