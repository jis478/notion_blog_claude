# Notion Blog Manager — Claude Instructions

## Project Overview
A full-stack blog manager that lets users write posts in a web UI, then uses an AI agent to review grammar and auto-publish to Notion via MCP.

## Stack
- **Backend**: FastAPI (`server.py`) with SSE streaming
- **Agent**: `notion_agent.py` — Claude + Notion MCP server (`@notionhq/notion-mcp-server` via npx)
- **Frontend**: `static/index.html` — single-page app, Tailwind CSS, vanilla JS
- **Model**: `claude-sonnet-4-6` with tool use for Notion API calls

## Key Files
- `server.py` — FastAPI app, lifespan manages MCP server startup/shutdown
- `notion_agent.py` — `NotionMCPManager` singleton, `list_posts()`, `publish_post()` async generator
- `static/index.html` — sidebar post list + editor + agent log panel

## Notion Database Properties
- `Name` (title)
- `status` (status): options are `Not started`, `draft`, `published`
- `tags` (multi_select)
- `published date` (date)

## Environment Variables
- `ANTHROPIC_API_KEY`
- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`

## How to Run
```bash
pip install -r requirements.txt
cp .env.example .env  # fill in keys
python server.py       # http://localhost:8000
```

## Coding Conventions
- Python: follow PEP8, use type hints, async/await throughout
- Keep the MCP session as a singleton — do not create per-request sessions (expensive npx startup)
- Frontend: no build step, no frameworks — plain JS + Tailwind CDN only
- SSE events must be JSON with a `type` field (`status`, `log`, `error`) or `{"done": true}`

## Code Review Guidelines
- Flag any security issues first (XSS, injection, exposed secrets)
- Check that MCP session lifecycle is handled correctly (start once, reuse, stop on shutdown)
- Verify SSE stream always emits `{"done": true}` even on error paths
- Keep frontend JS simple — no unnecessary abstractions
- Suggest fixes, don't just point out problems
