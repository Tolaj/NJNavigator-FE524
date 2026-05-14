"""agent.py — NJNavigator agent"""

from datetime import datetime

from dotenv import load_dotenv
from smolagents import OpenAIServerModel, ToolCallingAgent
from smolagents.agents import EMPTY_PROMPT_TEMPLATES, PromptTemplates
from smolagents.mcp_client import MCPClient

load_dotenv()

MCP_URL = {"url": "http://localhost:8000/mcp", "transport": "streamable-http"}

SYSTEM_PROMPT = """\
You are NJNavigator, a transit assistant for the NJ/NYC metro area.
Current date/time: {now}

Never guess stop names, times, or routes — always call the tools.
"""


def build_agent():
    now = datetime.now().strftime("%A %B %d %Y, %H:%M")
    mcp_client = MCPClient([MCP_URL], structured_output=False)
    mcp_client.__enter__()
    agent = ToolCallingAgent(
        tools=mcp_client.get_tools(),
        model=OpenAIServerModel(model_id="gpt-4o-mini"),
        prompt_templates=PromptTemplates(
            **{**EMPTY_PROMPT_TEMPLATES, "system_prompt": SYSTEM_PROMPT.format(now=now)}
        ),
    )
    return agent, mcp_client
