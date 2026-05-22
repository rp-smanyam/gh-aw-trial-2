import structlog
from agents import add_trace_processor
from langsmith.client import Client
from langsmith.wrappers import OpenAIAgentsTracingProcessor

from agent_leasing.settings import settings

logger = structlog.getLogger()


def configure():
    api_key = settings.langsmith_api_key
    api_url = settings.langsmith_endpoint
    # https://docs.smith.langchain.com/observability/how_to_guides/trace_with_openai_agents_sdk
    # TODO: when LANGSMITH_ENDPOINT references our self-hosted LangSmith, urllib3 complains about rewritten certs over VPN, even with pip-system-certs.
    #       Unclear why that's an issue here and not for mlops-agent-api, renter-ai-agent, langsmith-scripts, and other repos.
    #       I tried aligning urllib3, certifi, requests versions but that made no difference.
    #       Recommend we instead add the self-hosted LangSmith to list of hosts the VPN should not rewrite certs for.
    environment = settings.environment
    if api_key and api_url:
        client = Client(api_key=api_key, api_url=api_url)
        add_trace_processor(OpenAIAgentsTracingProcessor(client=client, project_name=f"{environment}-agent-leasing"))
        logger.info(f"LangSmith client and tracing configured for {environment}-agent-leasing.")
    else:
        logger.info("Skipping LangSmith client setup (missing LANGSMITH_API_KEY or LANGSMITH_ENDPOINT).")
