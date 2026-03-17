"""
Notion Blog Agent
-----------------
Polls Notion for pages tagged "blog", reviews grammar with Claude,
then marks them as Published automatically.

Setup:
  1. Create a Notion integration at https://www.notion.so/my-integrations
  2. Share your blog database with the integration
  3. Make sure your database has these properties:
       - Tags       (multi-select)  — add "blog" to trigger publishing
       - Status     (select)        — values: Draft, Published
       - Published Date (date)      — auto-set by agent
  4. Copy .env.example to .env and fill in your keys
  5. Run: python agent.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime

import anthropic
from anthropic.lib.tools.mcp import async_mcp_tool
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))

AGENT_PROMPT = """\
You are a blog publishing assistant for a Notion blog. Do the following steps in order:

**Step 1 — Find unpublished blog posts**
Search Notion for pages that:
- Have "blog" in their Tags property
- Do NOT have Status = "Published"

**Step 2 — For each qualifying page:**
1. Retrieve all the page's block content (paragraphs, headings, lists, etc.)
2. Review the text for grammar, spelling, punctuation, and clarity issues
3. Update each block with the corrected text
4. Update the page properties:
   - Set Status = "Published"
   - Set Published Date = {today}

**Rules:**
- Preserve the author's voice and meaning — only fix genuine errors
- Fix: typos, grammar mistakes, awkward phrasing, punctuation errors
- Keep: technical terms, code snippets, intentional style, structure
- If no pages qualify, just say "No new posts to publish."
- After publishing each post, briefly summarize what you changed

Today's date: {today}
"""


def check_env():
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not NOTION_TOKEN:
        missing.append("NOTION_TOKEN")
    if missing:
        print("Error: missing environment variables:", ", ".join(missing))
        print("Copy .env.example to .env and fill in your keys.")
        sys.exit(1)


async def run_agent():
    notion_headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
    }

    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@notionhq/notion-mcp-server"],
        env={
            **os.environ,
            "OPENAPI_MCP_HEADERS": json.dumps(notion_headers),
        },
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

            today = datetime.now().strftime("%Y-%m-%d")
            prompt = AGENT_PROMPT.format(today=today)

            runner = await client.beta.messages.tool_runner(
                model="claude-opus-4-6",
                max_tokens=8192,
                thinking={"type": "adaptive"},
                tools=[async_mcp_tool(t, session) for t in tools_result.tools],
                messages=[{"role": "user", "content": prompt}],
            )

            async for message in runner:
                for block in message.content:
                    if hasattr(block, "text") and block.text:
                        print(block.text)


async def main():
    check_env()
    print(f"Notion Blog Agent started. Polling every {POLL_INTERVAL}s.")
    print('Tag any Notion page with "blog" to trigger grammar review + publishing.\n')

    while True:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] Checking for new blog posts...")
        try:
            await run_agent()
        except Exception as e:
            print(f"[Error] {e}")

        print(f"Next check in {POLL_INTERVAL}s...\n")
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
