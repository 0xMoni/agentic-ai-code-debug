"""The Explanation handler."""

from __future__ import annotations

from agents.base import BaseAgent
from chains.lcel_chains import TextChain
from config.llm_provider import get_chat_model
from models.schemas import AgentName, Intent, PromptTechnique
from prompts.system_prompts import EXPLAINER_SYSTEM
from prompts.user_prompts import EXPLAINER_USER
from rag.retriever import policy_decision


class ExplainerAgent(BaseAgent):
    name = AgentName.EXPLAINER

    def run(self, code: str, user_request: str, intent: Intent) -> str:
        self.begin()

        decision = policy_decision(
            intent,
            self.name,
            rag_enabled=self.session.rag_enabled,
            settings=self.settings,
        )
        self.record_retrieval(decision)

        chain = TextChain(
            system_prompt=EXPLAINER_SYSTEM,
            user_template=EXPLAINER_USER,
            model=get_chat_model(self.settings),
            agent=self.name,
            technique=PromptTechnique.ZERO_SHOT,
            provider=self.settings.provider,
            model_name=self.settings.model_name,
        )
        self.record_technique(PromptTechnique.ZERO_SHOT)

        try:
            text, record = chain.invoke(
                {
                    "code": code,
                    "user_request": user_request.strip()
                    or "Explain it for someone learning Python.",
                }
            )
        except Exception as exc:
            self.fail(f"{type(exc).__name__}: {exc}")
            raise

        self.record_prompt(record)
        self.finish(f"Explanation produced ({len(text.split())} words).")
        return text
