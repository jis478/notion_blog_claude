"""
Notion agent — Claude + Notion MCP server to list and publish blog posts.

The MCP server starts ONCE at app startup and stays alive (persistent connection).
This avoids per-request npx startup overhead (~5-15s saved per call).

Working MCP tools (from @notionhq/notion-mcp-server):
  API-post-search          → search pages
  API-retrieve-a-database  → get database schema
  API-post-page            → create a page
  API-patch-page           → update page properties
  API-patch-block-children → append content blocks

Database property types (confirmed):
  Name           → title
  status         → status  (options: 'Not started', 'draft', 'published')
  tags           → multi_select
  published date → date
"""

import json
import os
from contextlib import AsyncExitStack
from datetime import datetime
from typing import AsyncGenerator

import anthropic
from anthropic.lib.tools.mcp import async_mcp_tool
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

# Singletons — initialised once at startup
anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


def _server_params() -> StdioServerParameters:
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
    }
    return StdioServerParameters(
        command="npx",
        args=["-y", "@notionhq/notion-mcp-server"],
        env={**os.environ, "OPENAPI_MCP_HEADERS": json.dumps(headers)},
    )


class NotionMCPManager:
    """Persistent MCP session — started once, reused for every request."""

    def __init__(self):
        self.session: ClientSession | None = None
        self.tools: list = []
        self._stack = AsyncExitStack()

    async def start(self):
        print("Starting Notion MCP server...")
        read, write = await self._stack.enter_async_context(
            stdio_client(_server_params())
        )
        self.session = await self._stack.enter_async_context(
            ClientSession(read, write)
        )
        await self.session.initialize()
        result = await self.session.list_tools()
        self.tools = result.tools
        print(f"Notion MCP ready — {len(self.tools)} tools available")

    async def stop(self):
        await self._stack.aclose()
        print("Notion MCP server stopped")

    def mcp_tools(self, names: list[str] | None = None):
        tools = self.tools
        if names:
            tools = [t for t in self.tools if t.name in names]
        return [async_mcp_tool(t, self.session) for t in tools]


mcp = NotionMCPManager()


async def list_posts() -> list[dict]:
    """Return all published blog posts directly via MCP tool — no LLM needed."""
    result = await mcp.session.call_tool(
        "API-post-search",
        {"filter": {"property": "object", "value": "page"}, "page_size": 100},
    )
    data = json.loads(result.content[0].text)

    db_id_clean = DATABASE_ID.replace("-", "")
    posts = []
    for page in data.get("results", []):
        # Filter to our database only
        parent = page.get("parent", {})
        parent_id = parent.get("database_id", "").replace("-", "")
        if parent_id != db_id_clean:
            continue

        props = page.get("properties", {})

        # Filter to published only
        status_val = (
            props.get("status", {}).get("status", {}) or {}
        ).get("name", "")
        if status_val.lower() != "published":
            continue

        # Extract fields
        title_parts = props.get("Name", {}).get("title", [])
        title = title_parts[0].get("plain_text", "Untitled") if title_parts else "Untitled"

        categories = [
            t["name"] for t in props.get("tags", {}).get("multi_select", [])
        ]

        date_obj = props.get("published date", {}).get("date") or {}
        published_date = date_obj.get("start")

        page_id = page["id"].replace("-", "")
        url = f"https://notion.so/{page_id}"

        posts.append({
            "id": page["id"],
            "title": title,
            "categories": categories,
            "published_date": published_date,
            "url": url,
        })

    return posts


async def publish_post(
    title: str, content: str, categories: list[str]
) -> AsyncGenerator[dict, None]:
    """Review grammar and publish a post to Notion. Yields SSE-ready dicts."""
    today = datetime.now().strftime("%Y-%m-%d")
    tags_json = json.dumps([{"name": c} for c in categories])

    prompt = f"""You are a blog publishing assistant. Complete these steps:

**Step 1 — Grammar review**
Review and fix grammar, spelling, and clarity in the post below.
Preserve the author's voice — only fix genuine errors.

Title: {title}

Content:
{content}

**Step 2 — Create the Notion page**
Call API-post-page with EXACTLY this structure:
{{
  "parent": {{"type": "database_id", "database_id": "{DATABASE_ID}"}},
  "properties": {{
    "Name": {{"title": [{{"text": {{"content": "<corrected title>"}}}}]}},
    "status": {{"status": {{"name": "published"}}}},
    "tags": {{"multi_select": {tags_json}}},
    "published date": {{"date": {{"start": "{today}"}}}}
  }}
}}

Important: status value must be "published" (lowercase), property name is "published date" (with space).

**Step 3 — Add content blocks**
Use the new page's ID and call API-patch-block-children:
{{
  "block_id": "<new page id>",
  "children": [
    {{"type": "paragraph", "paragraph": {{"rich_text": [{{"type": "text", "text": {{"content": "<paragraph text>"}}}}]}}}},
    ... one block per paragraph
  ]
}}

**Step 4 — Report**
Give the Notion URL (https://notion.so/<page-id-no-dashes>) and briefly describe grammar corrections."""

    yield {"type": "status", "message": "🔍 Reviewing grammar and publishing..."}

    runner = anthropic_client.beta.messages.tool_runner(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        tools=mcp.mcp_tools(["API-post-page", "API-patch-block-children", "API-patch-page"]),
        messages=[{"role": "user", "content": prompt}],
    )

    total_input = total_output = 0
    async for message in runner:
        if hasattr(message, "usage") and message.usage:
            total_input += getattr(message.usage, "input_tokens", 0)
            total_output += getattr(message.usage, "output_tokens", 0)
        for block in message.content:
            if hasattr(block, "text") and block.text:
                yield {"type": "log", "message": block.text}

    cost = (total_input * 3 + total_output * 15) / 1_000_000
    print(f"[usage] input={total_input} output={total_output} cost≈${cost:.4f}")
    yield {"type": "log", "message": f"💰 Tokens — input: {total_input}, output: {total_output}, cost ≈ ${cost:.4f}"}
