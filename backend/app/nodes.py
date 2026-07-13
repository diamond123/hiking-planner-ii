import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

from app.constants import (
    ALL_PREFERENCE_TOPICS,
    BAY_AREA_FALLBACK_LATLON,
    MAX_ATTEMPTS,
    MAX_PREFERENCE_ASKS,
    NON_CA_STATE_CODES,
    NON_CA_STATE_NAMES,
    NON_US_COUNTRY_KEYWORDS,
    PREFERENCE_TOPICS,
    PREFERENCE_TOPIC_LABELS,
)
from app.db import get_document_by_source
from app.geocode import geocode_location
from app.llm import condition_judge_llm, guardrail_llm, plan_writer_llm, slot_extractor_llm
from app.prompts import (
    ASK_DATE_MESSAGE,
    ASK_LOCATION_CLARIFICATION_MESSAGE,
    ASK_PREFERENCES_MESSAGE,
    EXHAUSTED_MESSAGE,
    EXTRACT_SLOTS_SYSTEM_PROMPT,
    GENERATE_PLAN_SYSTEM_PROMPT,
    GUARDRAIL_KEYWORDS,
    GUARDRAIL_SYSTEM_PROMPT,
    NO_CANDIDATES_MESSAGE,
    LOCATION_SCOPE_REJECTION_MESSAGE,
    REFUSAL_MESSAGE,
    TRAIL_JUDGE_SYSTEM_PROMPT,
    WEATHER_BAD_TEMPLATE,
    WEATHER_JUDGE_SYSTEM_PROMPT,
)
from app.qdrant_store import search_chunk
from app.state import HikingState
from app.tools import search_trail_conditions, search_weather

logger = logging.getLogger(__name__)

def _normalize_or_reject_location(location_text: str) -> tuple[str, bool]:
    normalized = location_text.strip()
    lowered = normalized.lower()

    has_non_ca_state_name = any(
        re.search(rf"\b{re.escape(name)}\b", lowered) for name in NON_CA_STATE_NAMES
    )
    has_non_ca_state_code = any(
        code != "CA" and re.search(rf"(?:^|,\s*){code}(?:$|,|\b)", normalized)
        for code in NON_CA_STATE_CODES
    )
    has_non_us_country = any(
        re.search(rf"\b{re.escape(country)}\b", lowered)
        for country in NON_US_COUNTRY_KEYWORDS
    )

    if has_non_ca_state_name or has_non_ca_state_code or has_non_us_country:
        return normalized, False

    has_california = bool(re.search(r"\bcalifornia\b|(?:^|,\s*)CA(?:$|,|\b)", normalized, re.IGNORECASE))
    has_usa = bool(
        re.search(
            r"\busa\b|\bu\.s\.a\b|\bunited states\b|(?:^|,\s*)US(?:$|,|\b)",
            normalized,
            re.IGNORECASE,
        )
    )

    if not has_california:
        normalized = f"{normalized}, California"
    if not has_usa:
        normalized = f"{normalized}, USA"

    return normalized, True


def _build_query_text(state: HikingState) -> str:
    parts = []
    prefs = state.get("preferences_text")
    if prefs and prefs != "no specific preference":
        parts.append(prefs)
    location = state.get("location_text")
    if location:
        parts.append(f"near {location}")
    if not parts:
        parts.append("a nice hike in the San Francisco Bay Area")
    return " ".join(parts)


def _extract_known_preference_topics(preferences_text: str | None) -> set[str]:
    if not preferences_text:
        return set()

    lowered = preferences_text.lower()
    known_topics: set[str] = set()
    for topic, keywords in PREFERENCE_TOPICS.items():
        if any(re.search(rf"\b{re.escape(keyword)}\b", lowered) for keyword in keywords):
            known_topics.add(topic)
    return known_topics


def _is_no_preference(preferences_text: str | None) -> bool:
    if not preferences_text:
        return False

    lowered = preferences_text.strip().lower()
    no_pref_variants = {
        "no preference",
        "no preferences",
        "no other preference",
        "no other preferences",
        "no specific preference",
        "no specific preferences",
    }
    return any(lowered.find(variant) != -1 for variant in no_pref_variants)


def reset_turn(state: HikingState) -> dict:
    return {
        "attempt_count": 0,
        "excluded_sources": [],
        "candidate_chunk": None,
        "candidate_document": None,
        "weather_result": None,
        "trail_result": None,
        "final_markdown": None,
        "route_signal": None,
    }


def guardrail(state: HikingState) -> dict:
    messages = state["messages"]
    latest_text = str(messages[-1].content) if messages else ""

    if any(kw in latest_text.lower() for kw in GUARDRAIL_KEYWORDS):
        blocked = True
    else:
        verdict = guardrail_llm.invoke(
            [SystemMessage(content=GUARDRAIL_SYSTEM_PROMPT), *messages[-6:]]
        )
        blocked = (not verdict.on_topic) or verdict.is_injection_attempt

    if blocked:
        return {"messages": [AIMessage(content=REFUSAL_MESSAGE)], "route_signal": "off_topic"}
    return {"route_signal": None}


def extract_slots(state: HikingState) -> dict:
    slots = None
    if not state.get("hiking_date") or not state.get("location_text") or \
        not state.get("preferences_text") or state.get('missing_preference_topics'):
        pacific_today = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
        system_prompt = (
            f"{EXTRACT_SLOTS_SYSTEM_PROMPT}\n\n"
            f"Today in US Pacific time is {pacific_today}. Resolve relative dates like "
            f"'today', 'tomorrow', 'this Saturday', and 'next weekend' against this "
            f"date. When you can "
            f"resolve a date precisely, return it as YYYY-MM-DD."
        )

        slots = slot_extractor_llm.invoke(
                [SystemMessage(content=system_prompt), *state["messages"]]
            )

    hiking_date = state.get("hiking_date") or (slots.hiking_date if slots else None)

    location_text = state.get("location_text") or (slots.location_text if slots else None)

    # preferences_text = ', '.join(filter(None, [state.get("preferences_text"), slots.preferences_text]))
    preferences_text = slots.preferences_text if slots else state.get("preferences_text")

    known_topics = _extract_known_preference_topics(preferences_text)
    missing_topics = [] if _is_no_preference(preferences_text) else sorted(ALL_PREFERENCE_TOPICS - known_topics)
    preferences_ask_count = state.get("preferences_ask_count", 0)

    # Stop asking for additional preferences after two asks.
    if preferences_ask_count >= MAX_PREFERENCE_ASKS:
        missing_topics = []

    location_latlon = state.get("location_latlon")
    if location_text and not location_latlon:
        logger.info("slot extraction location_text=%r", location_text)
        location_text, in_scope = _normalize_or_reject_location(location_text)

        if not in_scope:
            logger.info("location out of scope for Bay Area planner: %r", location_text)
            return {
                "messages": [AIMessage(content=LOCATION_SCOPE_REJECTION_MESSAGE)],
                "route_signal": "off_topic",
            }
        location_latlon = geocode_location(location_text)
        if location_latlon:
            logger.info("geocoding succeeded for %r: %s", location_text, location_latlon)
        else:
            logger.warning("geocoding returned no result for %r", location_text)

    return {
        "hiking_date": hiking_date,
        "location_text": location_text,
        "preferences_text": preferences_text,
        "location_latlon": location_latlon,
        "known_preference_topics": sorted(known_topics),
        "missing_preference_topics": missing_topics,
        "preferences_ask_count": preferences_ask_count,
    }


def ask_date(state: HikingState) -> dict:
    return {"messages": [AIMessage(content=ASK_DATE_MESSAGE)]}


def ask_preferences(state: HikingState) -> dict:
    ask_count = state.get("preferences_ask_count", 0)
    if ask_count >= MAX_PREFERENCE_ASKS:
        return {
            "preferences_ask_count": ask_count,
            "missing_preference_topics": [],
        }

    prompts: list[str] = []
    if not state.get("hiking_date"):
        prompts.append("your hiking date")
    if not state.get("location_text"):
        prompts.append("the Bay Area location you'd like to hike near")

    missing_topics = state.get("missing_preference_topics") or []
    prompts.extend(PREFERENCE_TOPIC_LABELS[t] for t in missing_topics if t in PREFERENCE_TOPIC_LABELS)

    if not prompts:
        text = f"Great, got it. {ASK_PREFERENCES_MESSAGE}"
    elif len(prompts) == 1:
        text = f"Great, got it. Could you share {prompts[0]}?"
    elif len(prompts) == 2:
        text = f"Great, got it. Could you share {prompts[0]} and {prompts[1]}?"
    else:
        text = (
            "Great, got it. Could you share "
            f"{', '.join(prompts[:-1])}, and {prompts[-1]}?"
        )

    return {
        "messages": [AIMessage(content=text)],
        "preferences_asked": True,
        "preferences_ask_count": ask_count + 1,
    }


def ask_location_clarification(state: HikingState) -> dict:
    return {"messages": [AIMessage(content=ASK_LOCATION_CLARIFICATION_MESSAGE)]}


def search_qdrant(state: HikingState) -> dict:
    writer = get_stream_writer()
    attempt = state.get("attempt_count", 0) + 1
    if attempt == 1:
        writer({"type": "status", "text": "Searching the trail database..."})
    else:
        writer({"type": "status", "text": "Searching the trail database for another option..."})

    query_text = _build_query_text(state)
    payload = search_chunk(
        query_text,
        excluded_sources=state.get("excluded_sources") or [],
        location_latlon=state.get("location_latlon"),
    )

    if payload is None:
        return {"attempt_count": attempt, "candidate_chunk": None}

    excluded = list(state.get("excluded_sources") or [])
    excluded.append(payload["metadata"]["source"])
    return {"attempt_count": attempt, "candidate_chunk": payload, "excluded_sources": excluded}


def no_candidates_response(state: HikingState) -> dict:
    return {"messages": [AIMessage(content=NO_CANDIDATES_MESSAGE)]}


def fetch_document(state: HikingState) -> dict:
    source = state["candidate_chunk"]["metadata"]["source"]
    content = get_document_by_source(source)
    return {"candidate_document": content}


def check_weather(state: HikingState) -> dict:
    writer = get_stream_writer()
    writer({"type": "status", "text": "Checking weather conditions..."})

    location_latlon = state.get("location_latlon") or BAY_AREA_FALLBACK_LATLON
    raw_text = search_weather(location_latlon, state["hiking_date"])
    if raw_text == "No weather information available.":
        return {
            "weather_result": {
                "ok": True,
                "reason": "No weather information available.",
                "raw": raw_text,
            }
        }

    judgment = condition_judge_llm.invoke(
        [SystemMessage(content=WEATHER_JUDGE_SYSTEM_PROMPT), HumanMessage(content=raw_text)]
    )
    logger.info("Weather judgment: %s", 'ok' if judgment.ok else 'bad')
    return {"weather_result": {"ok": judgment.ok, "reason": judgment.reason, "raw": raw_text}}


def weather_bad_response(state: HikingState) -> dict:
    location_label = state.get("location_text") or "the San Francisco Bay Area"
    text = WEATHER_BAD_TEMPLATE.format(
        date=state["hiking_date"],
        location=location_label,
        reason=state["weather_result"]["reason"],
    )
    return {"messages": [AIMessage(content=text)]}


def check_trail(state: HikingState) -> dict:
    writer = get_stream_writer()
    writer({"type": "status", "text": "Checking trail conditions..."})

    md = state["candidate_chunk"]["metadata"]
    raw_text = search_trail_conditions(
        md["title"],
        state.get("location_text"),
        state["hiking_date"],
    )
    judgment = condition_judge_llm.invoke(
        [SystemMessage(content=TRAIL_JUDGE_SYSTEM_PROMPT), HumanMessage(content=raw_text)]
    )
    if not judgment.ok:
        logger.info("Trail judgment: bad")
    return {"trail_result": {"ok": judgment.ok, "reason": judgment.reason, "raw": raw_text}}


def exhausted_response(state: HikingState) -> dict:
    return {"messages": [AIMessage(content=EXHAUSTED_MESSAGE)]}


def generate_plan(state: HikingState) -> dict:
    writer = get_stream_writer()
    writer({"type": "status", "text": "Preparing your hiking plan..."})

    md = state["candidate_chunk"]["metadata"]
    human_content = (
        f"Trail: {md['title']}\n"
        f"Hiking date: {state['hiking_date']}\n"
        f"User preferences: {state.get('preferences_text') or 'none specified'}\n\n"
        f"Weather conditions: {state['weather_result']['reason']}\n\n"
        f"Trail conditions: {state['trail_result']['reason']}\n\n"
        f"Document content:\n{state['candidate_document']}"
    )
    response = plan_writer_llm.invoke(
        [SystemMessage(content=GENERATE_PLAN_SYSTEM_PROMPT), HumanMessage(content=human_content)]
    )
    markdown = str(response.content)
    return {"final_markdown": markdown, "messages": [AIMessage(content=markdown)]}
