from langchain_openai import ChatOpenAI

from app.config import settings
from app.schemas import ConditionJudgment, ExtractedSlots, GuardrailVerdict

_base_llm = ChatOpenAI(model=settings.chat_model, api_key=settings.openai_api_key, temperature=0)

guardrail_llm = _base_llm.with_structured_output(GuardrailVerdict)
slot_extractor_llm = _base_llm.with_structured_output(ExtractedSlots)
condition_judge_llm = _base_llm.with_structured_output(ConditionJudgment)
plan_writer_llm = ChatOpenAI(model=settings.chat_model, api_key=settings.openai_api_key, temperature=0.4)
