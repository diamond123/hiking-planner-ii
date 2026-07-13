from fastapi import Header, HTTPException

from app.config import settings


async def verify_api_key(x_api_key: str = Header(default="")) -> None:
    if not x_api_key or x_api_key != settings.backend_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
