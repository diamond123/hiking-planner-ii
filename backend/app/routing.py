from app.constants import MAX_ATTEMPTS, MAX_PREFERENCE_ASKS
from app.state import HikingState


def route_after_guardrail(state: HikingState) -> str:
    return "end_turn" if state.get("route_signal") == "off_topic" else "extract_slots"


def route_after_extract_slots(state: HikingState) -> str:
    if state.get("route_signal") == "off_topic":
        return "end_turn"
    if not state.get("hiking_date"):
        return "ask_date"
    if state.get("location_text") and not state.get("location_latlon"):
        return "ask_location_clarification"
    missing_topics = state.get("missing_preference_topics") or []
    no_pref_variants = {
        "no preference",
        "no preferences",
        "no specific preference",
        "no specific preferences",
    }
    preferences_text = (state.get("preferences_text") or "").strip().lower()
    if (
        preferences_text not in no_pref_variants
        and missing_topics
        and state.get("preferences_ask_count", 0) < MAX_PREFERENCE_ASKS
    ):
        return "ask_preferences"
    return "check_weather"


def route_after_search(state: HikingState) -> str:
    return "no_candidates_response" if state.get("candidate_chunk") is None else "check_trail"


def route_after_weather(state: HikingState) -> str:
    weather_result = state.get("weather_result") or {}
    return "search_qdrant" if weather_result.get("ok", True) else "weather_bad_response"


def route_after_trail(state: HikingState) -> str:
    trail_result = state.get("trail_result") or {}
    if trail_result.get("ok", True):
        return "fetch_document"
    if state.get("attempt_count", 0) < MAX_ATTEMPTS:
        return "search_qdrant"
    return "exhausted_response"
