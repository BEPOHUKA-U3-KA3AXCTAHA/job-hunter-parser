"""FastAPI entrypoint. Delete or extend.

uvicorn app.entrypoints.api.server:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from app.modules.example import create_item, default_uow

app = FastAPI(title="Your project name")


class CreateItemRequest(BaseModel):
    name: str


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.post("/items")
async def create_item_endpoint(req: CreateItemRequest) -> dict:
    item = await create_item(default_uow(), name=req.name)
    return {"id": item.id, "name": item.name}
