"""This module defines the Katapult API"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from quart import Quart, request
from quart_schema import QuartSchema, validate_request, validate_response

app = Quart(__name__)
QuartSchema(app)


@app.get("/healthcheck")
async def healthcheck():
    return {"status": "alive"}


@app.post("/echo")
async def echo():
    print(request.is_json, request.mimetype)
    data = await request.get_json()
    return {"input": data, "extra": True}


class TodoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    due: datetime | None = None


class TodoOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    task: str
    due: datetime | None = None


@app.post("/todos/")
@validate_request(TodoIn)
@validate_response(TodoOut)
async def create_todo(data: TodoIn) -> TodoOut:
    return TodoOut(id=1, task=data.task, due=data.due)


def run() -> None:
    app.run()
