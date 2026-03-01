from __future__ import annotations

from dataclasses import dataclass

from auto_optimize_spec.models import AgentSpec, ProblemSpec, ProviderSpec


@dataclass
class AgentDraft:
    summary: str
    input_tokens: int
    output_tokens: int


class BaseProviderClient:
    def __init__(self, provider: ProviderSpec):
        self.provider = provider

    def propose(
        self,
        agent: AgentSpec,
        problem: ProblemSpec,
        attempt_index: int,
        feedback: str | None,
    ) -> AgentDraft:
        raise NotImplementedError


class MockProviderClient(BaseProviderClient):
    def propose(
        self,
        agent: AgentSpec,
        problem: ProblemSpec,
        attempt_index: int,
        feedback: str | None,
    ) -> AgentDraft:
        base = (
            f"Agent {agent.id} ({agent.persona}) attempt {attempt_index} for problem {problem.id}. "
            f"Goal: {problem.goal}"
        )
        if feedback:
            base += f" Feedback considered: {feedback[:180]}"
        # Token estimates are placeholders used for agent-cost accounting in skeleton mode.
        input_tokens = max(
            200, len(problem.goal) + len(agent.persona) + (len(feedback or "") // 2)
        )
        output_tokens = max(120, len(base) // 2)
        return AgentDraft(
            summary=base, input_tokens=input_tokens, output_tokens=output_tokens
        )


class UnsupportedProviderClient(BaseProviderClient):
    def propose(
        self,
        agent: AgentSpec,
        problem: ProblemSpec,
        attempt_index: int,
        feedback: str | None,
    ) -> AgentDraft:
        raise NotImplementedError(
            f"Provider type '{self.provider.type}' is not wired yet. "
            "Use the mock provider path first, then add concrete adapter implementations."
        )


def create_provider_client(provider: ProviderSpec) -> BaseProviderClient:
    # Skeleton behavior: all provider types route to a mock client until real adapters are implemented.
    # This keeps the end-to-end execution flow functional with any provider config.
    return MockProviderClient(provider)
