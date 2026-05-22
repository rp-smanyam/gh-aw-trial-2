# Endpoints

See below for the endpoints used in each environment.

This list covers external dependencies for Alpha/Beta/Prod. For local dev URLs and how to run the server, see [README.md](../README.md).

### Alpha
- LangSmith API - https://api.smith.langchain.com
- Facilities MCP server - https://facilities-ai-sat.realpage.com/facilities-service-request-mcp/mcp
- Knock MCP server - https://alpha-mcp-knock.knocktest.com/mcp/
- Loft MCP server - https://alpha-mcp-loft.knocktest.com/mcp/
- OneSite MCP server - https://internalapi-qa.realpage.com/os/mcp
- Facilities Thinker API - https://facilities-ai-sat.realpage.com/facilities-resident-thinker/v2/thinker
- LDP RP API URL - https://internalapi-sat.realpage.com/renter-read
- Books host - https://internalapi-qa.realpage.com
  - https://internalapi-qa.realpage.com/books/translate/v2/companyinstance/{uc_company_id}/OS
  - https://internalapi-qa.realpage.com/books/translate/v2/propertyinstance/{uc_property_id}/OS
  - https://internalapi-qa.realpage.com/settings/v1/SET/companies/{company_id}/properties/{property_id}
- Emergency dispatch URL - https://twl-qa-rpcc.realpage.com/inboundIVR/api/voice/ResAICreateEngineDispatch/{lo_property_id}
- Knock internal API URL - https://alpha-api.knocktest.com
  - https://alpha-api.knocktest.com/v1/relay/voice/handlers/hangup-with-recording
  - https://alpha-api.knocktest.com/v1/relay/voice/clay/callback
  - https://alpha-api.knocktest.com/v1/internal/residents/{resident_id}/activity

### Beta
- LangSmith API - https://api.smith.langchain.com
- Facilities MCP server - https://facilities-ai-sat.realpage.com/facilities-service-request-mcp/mcp
- Knock MCP server - https://beta-mcp-knock.knocktest.com/mcp/
- Loft MCP server - https://beta-mcp-loft.knocktest.com/mcp/
- OneSite MCP server - https://internalapi-sat.realpage.com/os/mcp
- Facilities Thinker API - https://facilities-ai-sat.realpage.com/facilities-resident-thinker/v2/thinker
- LDP RP API URL - https://internalapi-sat.realpage.com/renter-read
- Books host - https://internalapi-sat.realpage.com
  - https://internalapi-sat.realpage.com/books/translate/v2/companyinstance/{uc_company_id}/OS
  - https://internalapi-sat.realpage.com/books/translate/v2/propertyinstance/{uc_property_id}/OS
  - https://internalapi-sat.realpage.com/settings/v1/SET/companies/{company_id}/properties/{property_id}
- Emergency dispatch URL - https://twl-qa-rpcc.realpage.com/inboundIVR/api/voice/ResAICreateEngineDispatch/{lo_property_id}
- Knock internal API URL - https://ccnp-api.knocktest.com
  - https://ccnp-api.knocktest.com/v1/relay/voice/handlers/hangup-with-recording
  - https://ccnp-api.knocktest.com/v1/relay/voice/clay/callback
  - https://ccnp-api.knocktest.com/v1/internal/residents/{resident_id}/activity

### Prod
- LangSmith API - https://api.smith.langchain.com
- Facilities MCP server - https://facilities-ai.realpage.com/facilities-service-request-mcp/mcp
- Knock MCP server - https://mcp-knock.knockcrm.com/mcp/
- Loft MCP server - https://mcp-loft.knockcrm.com/mcp/
- OneSite MCP server - https://internalapi.realpage.com/os/mcp
- Facilities Thinker API - https://facilities-ai.realpage.com/facilities-resident-thinker/v2/thinker
- LDP RP API URL - https://internalapi.realpage.com/renter-read
- Books host - https://internalapi.realpage.com
  - https://internalapi.realpage.com/books/translate/v2/companyinstance/{uc_company_id}/OS
  - https://internalapi.realpage.com/books/translate/v2/propertyinstance/{uc_property_id}/OS
  - https://internalapi.realpage.com/settings/v1/SET/companies/{company_id}/properties/{property_id}
- Emergency dispatch URL - https://twl-rpcc.realpage.com/inboundIVR/api/voice/ResAICreateEngineDispatch/{lo_property_id}
- Knock internal API URL - https://api.knockrentals.com
  - https://api.knockrentals.com/v1/relay/voice/handlers/hangup-with-recording
  - https://api.knockrentals.com/v1/relay/voice/clay/callback
  - https://api.knockrentals.com/v1/internal/residents/{resident_id}/activity

