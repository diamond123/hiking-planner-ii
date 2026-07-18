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

    # index into `messages` where the current planning request starts; messages
    # before this index belong to an already-completed prior request and are
    # excluded from slot extraction, so a finished plan's date/location/prefs
    # don't leak into the next one. Persists across turns via the checkpointer.
    request_start_index: int

    # slot-filling, persists across turns via the checkpointer
    hiking_date: str | None
    date_rejection_reason: str | None
    preferences_text: str | None
    preferences_asked: bool
    preferences_ask_count: int
    known_preference_topics: list[str]
    missing_preference_topics: list[str]
    location_text: str | None
    location_latlon: dict | None

    # every candidate source shown so far this planning request (seeds
    # excluded_sources on regenerate); regenerations used so far this
    # planning request, checked against settings.planning_limit. Both persist
    # across turns via the checkpointer, reset only when a new request starts.
    plan_source_history: list[str]
    regenerate_count: int

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
