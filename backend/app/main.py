import json
import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from app.graph import compiled_graph
from app.rate_limit import enforce_rate_limit
from app.schemas import ChatRequest, TurnstileVerifyRequest
from app.security import verify_api_key
from app.turnstile import verify_turnstile_token

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


@app.post("/api/chat", dependencies=[Depends(enforce_rate_limit), Depends(verify_api_key)])
async def chat(req: ChatRequest):
    config = {"configurable": {"thread_id": req.session_id}}
    graph_input = {"messages": [HumanMessage(content=req.message)]}

    async def event_stream():
        try:
            final_state = None
            async for mode, chunk in compiled_graph.astream(
                graph_input, config, stream_mode=["custom", "values"]
            ):
                if mode == "custom":
                    yield json.dumps(chunk) + "\n"
                elif mode == "values":
                    final_state = chunk

            markdown = None
            if final_state:
                markdown = final_state.get("final_markdown")
                if not markdown and final_state.get("messages"):
                    markdown = final_state["messages"][-1].content

            yield json.dumps(
                {"type": "final", "markdown": markdown or "", "session_id": req.session_id}
            ) + "\n"
        except Exception:
            logger.exception("chat graph failed for session %s", req.session_id)
            yield json.dumps(
                {"type": "error", "text": "Something went wrong preparing your plan. Please try again."}
            ) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
