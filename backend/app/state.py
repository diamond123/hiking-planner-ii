from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

RouteSignal = Literal[
    "off_topic",
    "need_date",
    "need_prefs",
    "search",
    "no_candidates",
    "weather_bad",
    "trail_bad_retry",
    "trail_bad_exhausted",
    "done",
]


class HikingState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]

    # slot-filling, persists across turns via the checkpointer
    hiking_date: str | None
    preferences_text: str | None
    preferences_asked: bool
    preferences_ask_count: int
    known_preference_topics: list[str]
    missing_preference_topics: list[str]
    location_text: str | None
    location_latlon: dict | None

    # within-turn retry loop state, reset at the start of every invocation
    attempt_count: int
    excluded_sources: list[str]

    # current candidate + results, reset at the start of every invocation
    candidate_chunk: dict | None
    candidate_document: str | None
    weather_result: dict | None
    trail_result: dict | None
    final_markdown: str | None

    route_signal: RouteSignal | None
