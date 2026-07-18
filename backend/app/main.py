import asyncio
import json
import logging
import re

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.config import settings
from app.email_sender import send_plan_email
from app.graph import compiled_graph
from app.prompts import PLAN_READY_MESSAGE
from app.rate_limit import enforce_rate_limit
from app.schemas import (
    ChatRequest,
    EndSessionRequest,
    RegeneratePlanRequest,
    SendPlanEmailRequest,
    TurnstileVerifyRequest,
)
from app.security import verify_api_key
from app.turnstile import verify_turnstile_token

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Hiking Planner API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/verify-turnstile", dependencies=[Depends(enforce_rate_limit), Depends(verify_api_key)])
async def verify_turnstile(req: TurnstileVerifyRequest, request: Request):
    remote_ip = request.client.host if request.client else None
    if not verify_turnstile_token(req.token, remote_ip):
        raise HTTPException(status_code=403, detail="Human verification failed")
    return {"success": True}


@app.post("/api/end-session", dependencies=[Depends(enforce_rate_limit), Depends(verify_api_key)])
async def end_session(req: EndSessionRequest):
    await compiled_graph.checkpointer.adelete_thread(req.session_id)
    return {"success": True}


def _build_final_payload(final_state: dict | None, session_id: str) -> dict:
    markdown = None
    plan_complete = False
    if final_state:
        markdown = final_state.get("final_markdown")
        plan_complete = bool(markdown)
        if not markdown and final_state.get("messages"):
            markdown = final_state["messages"][-1].content

    payload = {
        "type": "final",
        "markdown": markdown or "",
        "session_id": session_id,
        "plan_complete": plan_complete,
    }
    if plan_complete:
        regenerate_count = final_state.get("regenerate_count", 0)
        payload["regenerate_remaining"] = max(settings.planning_limit - regenerate_count, 0)
    return payload


async def _stream_graph(graph_input, config: dict, session_id: str):
    try:
        final_state = None
        async for mode, chunk in compiled_graph.astream(
            graph_input, config, stream_mode=["custom", "values"]
        ):
            if mode == "custom":
                yield json.dumps(chunk) + "\n"
            elif mode == "values":
                final_state = chunk

        yield json.dumps(_build_final_payload(final_state, session_id)) + "\n"
    except Exception:
        logger.exception("graph run failed for session %s", session_id)
        yield json.dumps(
            {"type": "error", "text": "Something went wrong preparing your plan. Please try again."}
        ) + "\n"


@app.post("/api/chat", dependencies=[Depends(enforce_rate_limit), Depends(verify_api_key)])
async def chat(req: ChatRequest):
    config = {"configurable": {"thread_id": req.session_id}}
    graph_input = {"messages": [HumanMessage(content=req.message)]}
    return StreamingResponse(
        _stream_graph(graph_input, config, req.session_id), media_type="application/x-ndjson"
    )


@app.post("/api/regenerate-plan", dependencies=[Depends(enforce_rate_limit), Depends(verify_api_key)])
async def regenerate_plan(req: RegeneratePlanRequest):
    config = {"configurable": {"thread_id": req.session_id}}
    state_snapshot = await compiled_graph.aget_state(config)
    values = state_snapshot.values if state_snapshot else {}

    if not values.get("final_markdown"):
        raise HTTPException(status_code=400, detail="No plan to regenerate yet.")

    regenerate_count = values.get("regenerate_count", 0)
    if regenerate_count >= settings.planning_limit:
        raise HTTPException(status_code=400, detail="Regeneration limit reached.")

    graph_input = Command(
        goto="search_qdrant",
        update={
            "messages": [HumanMessage(content="Show me a different trail.")],
            "excluded_sources": values.get("plan_source_history") or [],
            "attempt_count": 0,
            "candidate_chunk": None,
            "candidate_document": None,
            "trail_result": None,
            "final_markdown": None,
            "regenerate_count": regenerate_count + 1,
        },
    )
    return StreamingResponse(
        _stream_graph(graph_input, config, req.session_id), media_type="application/x-ndjson"
    )


@app.post("/api/send-plan-email", dependencies=[Depends(enforce_rate_limit), Depends(verify_api_key)])
async def send_plan_email_endpoint(req: SendPlanEmailRequest):
    if not EMAIL_RE.match(req.email):
        raise HTTPException(status_code=400, detail="That doesn't look like a valid email address.")
    if not settings.resend_api_key:
        raise HTTPException(status_code=500, detail="Email sending isn't configured.")

    config = {"configurable": {"thread_id": req.session_id}}
    state_snapshot = await compiled_graph.aget_state(config)
    values = state_snapshot.values if state_snapshot else {}
    plan_markdown = values.get("final_markdown")
    if not plan_markdown:
        raise HTTPException(status_code=400, detail="No completed plan to email yet.")

    # PLAN_READY_MESSAGE ("## 🥾 Here you go!\n\n---") is chat-bubble framing
    # for the lead-in to a freshly generated plan - it reads as a dangling
    # non-sequitur at the top of a standalone email, so strip it before sending.
    email_markdown = plan_markdown
    if email_markdown.startswith(PLAN_READY_MESSAGE):
        email_markdown = email_markdown[len(PLAN_READY_MESSAGE):].lstrip("\n")

    try:
        await asyncio.to_thread(send_plan_email, req.email, email_markdown)
    except Exception:
        logger.exception("failed to send plan email for session %s", req.session_id)
        raise HTTPException(status_code=502, detail="Couldn't send the email. Please try again.")

    await compiled_graph.checkpointer.adelete_thread(req.session_id)
    return {"success": True}
