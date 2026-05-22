#:sdk Aspire.AppHost.Sdk@13.0.2
#:package Aspire.Hosting.JavaScript@13.0.2
#:package Aspire.Hosting.OpenAI@13.0.2
#:package Aspire.Hosting.Python@13.0.2
#:package Aspire.Hosting.Redis@13.0.2
#:package CommunityToolkit.Aspire.Hosting.McpInspector@13.0.0-beta.443
#:package CommunityToolkit.Aspire.Hosting.OpenTelemetryCollector@13.0.0

var builder = DistributedApplication.CreateBuilder(args);

// Optional sandbox mode: set SANDBOX_NAME env var to use real sandbox endpoints
// Example: SANDBOX_NAME=gt6ym2bvx aspire run aspire-app/apphost.cs
var sandboxName = Environment.GetEnvironmentVariable("SANDBOX_NAME");
var useSandbox = !string.IsNullOrEmpty(sandboxName);

var langSmithApiKey = builder.AddParameter("langsmith-api-key", secret: true);
var openai = builder.AddOpenAI("openai");

var otelCollector = builder.AddOpenTelemetryCollector("opentelemetry-collector")
    .WithConfig("./otel-collector-config.yaml")
    .WithEnvironment("LANGSMITH_API_KEY", langSmithApiKey);
    //.WithAppForwarding();

var redis = builder.AddRedis("redis")
    .WithPassword(null)  //because the agent doesn't support auth for redis yet
    .WithDataVolume()
    .WithPersistence();

redis.WithRedisInsight(redisInsightResource => {
    redisInsightResource.WithParentRelationship(redis);
});

var mockServer = builder.AddContainer("mock-server", "mockserver/mockserver", "5.14.0")
    .WithHttpEndpoint(port: 1080, targetPort: 1080, name: "http")
    .WithExternalHttpEndpoints()
    .WithBindMount("../tests", "/tests")
    .WithEnvironment("MOCKSERVER_INITIALIZATION_JSON_PATH", "/tests/mockserver-expectations.json")
    .WithEnvironment("MOCKSERVER_WATCH_INITIALIZATION_JSON", "true")
    .WithArgs("-logLevel", "INFO", "-serverPort", "1080")
    .WithUrls(context =>
    {
        context.Urls.Clear();
        context.Urls.Add(new ResourceUrlAnnotation{
            Url = "/mockserver/dashboard",
            DisplayText = "Dashboard",
            Endpoint = context.GetEndpoint("http")
        });
    });

var mcpServer = builder.AddDockerfile("mcp", "..", "MCP.dockerfile")
    .WithHttpEndpoint(port: 8042, targetPort: 8042, name: "http")
    .WithExternalHttpEndpoints();

var mcpInspector = builder.AddMcpInspector("inspector", options => {
        options.InspectorVersion = "0.18.0";
    })
    .WithMcpServer(mcpServer, true, McpTransportType.StreamableHttp, "")
    .WaitFor(mcpServer)
    .WithParentRelationship(mcpServer);

var agent = builder.AddUvicornApp("agent-leasing", "../src/agent_leasing", "server:app")
    .WaitFor(mcpServer)
    .WaitFor(mockServer)
    .WaitFor(redis)
    .WaitFor(otelCollector)
    .WithReference(redis)
    .WithUv()
    .WithExternalHttpEndpoints()
    .WithEnvironment("KNOCK_MCP_SERVER", mcpServer.GetEndpoint("http"))
    .WithEnvironment("KNOCK_MCP_AUTH_TOKEN_ENDPOINT", $"{mockServer.GetEndpoint("http")}/login/identity/connect/token")
    .WithEnvironment("LDP_LOGIN_TOKEN_ENDPOINT", $"{mockServer.GetEndpoint("http")}/login/identity/connect/token")
    .WithEnvironment("LDP_RP_API_URL", mockServer.GetEndpoint("http"))
    .WithEnvironment("LOFT_MCP_SERVER", mcpServer.GetEndpoint("http"))
    .WithEnvironment("LOFT_MCP_AUTH_TOKEN_ENDPOINT", $"{mockServer.GetEndpoint("http")}/login/identity/connect/token")
    .WithEnvironment("BOOKS_HOST", $"{mockServer.GetEndpoint("http")}/")
    .WithEnvironment("BOOKS_AUTH_ENDPOINT", $"{mockServer.GetEndpoint("http")}/login/identity/connect/token")
    .WithEnvironment("REDIS_ENABLED", "true")
    .WithEnvironment("OTEL_ENABLED", "true")
    //comment out OTEL_EXPORTER_* env vars below to send directly to Aspire
    .WithEnvironment("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    .WithEnvironment("OTEL_EXPORTER_OTLP_ENDPOINT", otelCollector.GetEndpoint("http"))
    .WithEnvironment("LANGSMITH_TRACING", "true")
    .WithEnvironment("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    .WithEnvironment("LANGSMITH_API_KEY", langSmithApiKey)
    .WithUrls(context =>
    {
        context.Urls.Clear();
        context.Urls.Add(new ResourceUrlAnnotation{
            Url = "/chatbot",
            DisplayText = "Chatbot",
            Endpoint = context.GetEndpoint("http")
        });
        context.Urls.Add(new ResourceUrlAnnotation{
            Url = "/docs",
            DisplayText = "OpenAPI Docs",
            Endpoint = context.GetEndpoint("http")
        });
        context.Urls.Add(new ResourceUrlAnnotation{
            Url = "/voice-ui",
            DisplayText = "Voice UI",
            Endpoint = context.GetEndpoint("http")
        });
    });

if (useSandbox)
{
    // Sandbox mode: use real MCP servers with sandbox-specific endpoints
    var onesiteMcpServer = builder.AddContainer("onesite-mcp", "artifacts.realpage.com/rp-docker-local/one/mcpserver", "20260109.2")
        .WithHttpEndpoint(port: 8043, targetPort: 8011, name: "http")
        .WithExternalHttpEndpoints()
        .WithEnvironment("OAuth_issuer", $"https://{sandboxName}-upfm-ui.dev.sb.realpage.com/login/identity")
        .WithEnvironment("OAuth_audiences", $"https://{sandboxName}-upfm-ui.dev.sb.realpage.com/login/identity/resources")
        .WithEnvironment("OAuth_scopes", "onesite-lr-mcp,onesite-ldp-mcp")
        .WithEnvironment("OS_KONG_URL", $"https://internalapi-sandbox.realpage.com/{sandboxName}/os/")
        .WithEnvironment("OS_API_KEY", "tYs8pcJ0frVzjHdtTce2PdWAKR9u7V")
        .WithEnvironment("OS_CLIENT_ID", "your-client-id-here")
        .WithEnvironment("LOG_LEVEL", "DEBUG")
        .WithEnvironment("OTEL_ENABLED", "true")
        .WithEnvironment("OTEL_SERVICE_NAME", "onesite-mcp")
        .WithEnvironment("OTEL_EXPORTER_OTLP_ENDPOINT", otelCollector.GetEndpoint("grpc"));

    var facilitiesMcpServer = builder.AddDockerfile("facilities-mcp", "../../facilities-service-request-mcp-server", "docker/MCP.Dockerfile")
        .WithHttpEndpoint(targetPort: 8000, name: "http")
        .WithExternalHttpEndpoints()
        .WithEnvironment("LOG_LEVEL", "DEBUG")
        .WithEnvironment("OTEL_EXPORTER_OTLP_ENDPOINT", otelCollector.GetEndpoint("http"))
        .WithEnvironment("UNIFIED_LOGIN_TOKEN_ENDPOINT", $"https://{sandboxName}-upfm-ui.dev.sb.realpage.com/login/identity/connect/token")
        .WithEnvironment("UNIFIED_LOGIN_AUTHORITY", $"https://{sandboxName}-upfm-ui.dev.sb.realpage.com/login/identity")
        .WithEnvironment("UNIFIED_LOGIN_SCOPES", "facilitiescommonapi facilitiesinspectionsapi facilitiesservicerequestsapi")
        .WithEnvironment("UNIFIED_LOGIN_AUDIENCE", "facilitiescommonapi facilitiesinspectionsapi facilitiesservicerequestsapi")
        .WithEnvironment("FACILITIES_CLIENT_ID", "ai-agent-facilities")
        .WithEnvironment("FACILITIES_CLIENT_SECRET", "SECRET")
        .WithEnvironment("FACILITIES_CLIENT_SCOPES", "facilitiescommonapi facilitiesinspectionsapi facilitiesservicerequestsapi")
        .WithEnvironment("FACILITIES_API_BASE_URL", $"https://internalapi-sandbox.realpage.com/{sandboxName}")
        .WithEnvironment("FACILITIES_COMMON_API_BASE_URL", $"https://{sandboxName}-nuef-facilities-commonapi-api.dev.sb.realpage.com")
        .WithReference(openai, "OPENAI_API")
        .WithReference(redis)
        .WithEnvironment("CACHE_CONFIGURATION", "redis://redis:6379/0");

    agent
        .WithEnvironment("FACILITIES_MCP_SERVER", $"{facilitiesMcpServer.GetEndpoint("http")}/facilities-service-request-mcp/mcp")
        .WithEnvironment("FACILITIES_MCP_AUTH_TOKEN_ENDPOINT", $"https://{sandboxName}-upfm-ui.dev.sb.realpage.com/login/identity/connect/token")
        .WithEnvironment("FACILITIES_MCP_AUTH_CLIENT_ID", "ai-agent-facilities")
        .WithEnvironment("FACILITIES_MCP_AUTH_SCOPES", "facilitiescommonapi facilitiesinspectionsapi facilitiesservicerequestsapi unifiedsettingsapi")
        .WithEnvironment("ONESITE_MCP_SERVER", onesiteMcpServer.GetEndpoint("http"))
        .WithEnvironment("ONESITE_MCP_AUTH_TOKEN_ENDPOINT", $"https://{sandboxName}-upfm-ui.dev.sb.realpage.com/login/identity/connect/token")
        .WithEnvironment("ONESITE_MCP_AUTH_CLIENT_ID", "resident-ai-agent");
}
else
{
    // Mock mode (default): use local MCP server with mock endpoints
    agent
        .WithEnvironment("FACILITIES_MCP_SERVER", mcpServer.GetEndpoint("http"))
        .WithEnvironment("FACILITIES_MCP_AUTH_TOKEN_ENDPOINT", $"{mockServer.GetEndpoint("http")}/login/identity/connect/token")
        .WithEnvironment("ONESITE_MCP_SERVER", mcpServer.GetEndpoint("http"))
        .WithEnvironment("ONESITE_MCP_AUTH_TOKEN_ENDPOINT", $"{mockServer.GetEndpoint("http")}/login/identity/connect/token");
}

agent.WithEnvironment("ASK_ENDPOINT", $"{agent.GetEndpoint("http")}/v1/agent/ask");

openai.WithParentRelationship(agent);
agent.WithReference(openai, "OPENAI_API");

langSmithApiKey.WithParentRelationship(agent);

builder.Build().Run();
