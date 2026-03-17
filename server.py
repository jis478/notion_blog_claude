"""
FastAPI server — serves the UI and exposes the Notion blog agent via REST + SSE.
The Notion MCP server starts once at app startup and stays alive.
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from notion_agent import list_posts, mcp, publish_post


@asynccontextmanager
async def lifespan(app: FastAPI):
    await mcp.start()   # start MCP server once
    yield
    await mcp.stop()    # clean up on shutdown


app = FastAPI(title="Notion Blog Manager", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    return Path("static/index.html").read_text()


@app.get("/api/posts")
async def get_posts():
    try:
        return await list_posts()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


class PublishRequest(BaseModel):
    title: str
    content: str
    categories: list[str]


@app.post("/api/publish")
async def publish(request: PublishRequest):
    async def stream():
        try:
            async for event in publish_post(
                request.title, request.content, request.categories
            ):
                yield f"data: {json.dumps(event)}\n\n"
            yield 'data: {"done": true}\n\n'
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            yield 'data: {"done": true}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
