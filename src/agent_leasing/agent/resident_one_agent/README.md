# DESIGN

The `resident_one_agent` folder contains two agent implementations: voice and text-based. 
The application provides an HTTP API and a websockets endpoint to interact with these agents. 
The entrypoints for all interaction are defined in `server.py`.

The text-based agent is defined in `agent.py` as `ResidentAgent`. It is used for chat, email and SMS communication. 
The internal name of this agent is `resident-one-agent`. It maps to the product names `RESIDENT_ONE_CHAT`, 
`RESIDENT_ONE_SMS` and `RESIDENT_ONE_EMAIL`.

The voice agent is defined in `realtime.py` as `ResidentRealtimeResponderAgent`. 
The internal name of this agent is `resident-one-realtime-agent`. It maps to the product name `RESIDENT_ONE_VOICE`. 
The agent is only used for voice communication; it uses a responder/thinker pattern to handle incoming requests. 
The responder handles requests but delegates to a thinker to do the actual work. The thinker is an instance 
of `ResidentAgent`, the same agent used for text-based communication. The thinker agent is used as a tool leveraging 
the OpenAI Agents SDK agent-as-tool feature. 

With text-based communication, `ResidentAgent` is an independent single agent. With voice communication
the `ResidentAgent` is not directly used; it is subordinate to the `ResidentRealtimeResponderAgent`.

Both the text-based and voice agents are subclassed from the `BaseResidentAgent`, which defines initialization and 
context manager behavior. When the agent is entered into (`__aenter__`) it:
1. Fetches modules and saves list of disabled modules in the context; modules control what features are toggled on and off.
2. Initializes MCP servers. This must happen after modules are set.
3. Pre-fetches Property Overview and Insights and saves them in the context.

The agents use Jinja2 templating to generate prompts. The text-based agent (`resident-one-agent`) uses `INSTRUCTIONS.md` 
(optimized for gpt-5.1) and the voice agent uses `VOICE_RESPONDER.md` (optimized for gpt-realtime-2). Depending on the communication 
channel and what modules are enabled, these prompts can look very different to the LLM.

## File Structure

```
resident_one_agent/
├── INSTRUCTIONS.md        # Jinja2 prompt template for text-based agent (chat, email, SMS) and thinker
├── VOICE_RESPONDER.md     # Jinja2 prompt template for voice agent responder
├── agent.py               # Text-based agent and thinker tool (ResidentAgent)
├── agent_helper.py        # Helper functions
└── realtime.py            # Voice agent (ResidentRealtimeResponderAgent)
``` 