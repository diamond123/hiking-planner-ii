from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from app import nodes, routing
from app.state import HikingState


def build_graph():
    graph = StateGraph(HikingState)

    graph.add_node("reset_turn", nodes.reset_turn)
    graph.add_node("guardrail", nodes.guardrail)
    graph.add_node("extract_slots", nodes.extract_slots)
    graph.add_node("ask_date", nodes.ask_date)
    graph.add_node("ask_location_clarification", nodes.ask_location_clarification)
    graph.add_node("ask_preferences", nodes.ask_preferences)
    graph.add_node("search_qdrant", nodes.search_qdrant)
    graph.add_node("no_candidates_response", nodes.no_candidates_response)
    graph.add_node("fetch_document", nodes.fetch_document)
    graph.add_node("check_weather", nodes.check_weather)
    graph.add_node("weather_bad_response", nodes.weather_bad_response)
    graph.add_node("check_trail", nodes.check_trail)
    graph.add_node("exhausted_response", nodes.exhausted_response)
    graph.add_node("generate_plan", nodes.generate_plan)

    graph.set_entry_point("reset_turn")
    graph.add_edge("reset_turn", "guardrail")

    graph.add_conditional_edges(
        "guardrail",
        routing.route_after_guardrail,
        {"end_turn": END, "extract_slots": "extract_slots"},
    )

    graph.add_conditional_edges(
        "extract_slots",
        routing.route_after_extract_slots,
        {
            "end_turn": END,
            "ask_date": "ask_date",
            "ask_location_clarification": "ask_location_clarification",
            "ask_preferences": "ask_preferences",
            "check_weather": "check_weather",
        },
    )

    graph.add_edge("ask_date", END)
    graph.add_edge("ask_location_clarification", END)
    graph.add_edge("ask_preferences", END)

    graph.add_conditional_edges(
        "check_weather",
        routing.route_after_weather,
        {"search_qdrant": "search_qdrant", "weather_bad_response": "weather_bad_response"},
    )
    graph.add_edge("weather_bad_response", END)

    graph.add_conditional_edges(
        "search_qdrant",
        routing.route_after_search,
        {"no_candidates_response": "no_candidates_response", "check_trail": "check_trail"},
    )
    graph.add_edge("no_candidates_response", END)

    graph.add_conditional_edges(
        "check_trail",
        routing.route_after_trail,
        {
            "fetch_document": "fetch_document",
            "search_qdrant": "search_qdrant",
            "exhausted_response": "exhausted_response",
        },
    )
    graph.add_edge("fetch_document", "generate_plan")
    graph.add_edge("exhausted_response", END)
    graph.add_edge("generate_plan", END)

    return graph.compile(checkpointer=InMemorySaver())


compiled_graph = build_graph()
