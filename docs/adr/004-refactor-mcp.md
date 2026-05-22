# 4. Refactor MCP Server Architecture

## Status

Accepted (2025-08-08)

## Context

We need to support multiple MCP servers in a thread-safe way, but we don't want an overly complex solution. Only one MCP server is supported, but we'll need many, especially with the introduction of resident.

## Decision

Refactor for maximum flexibility; MCP servers become an agent concern. Use an agent class as a context manager to ensure resources are cleaned up and for thread safety.

## Consequences

Each agent is responsible for their own MCP servers, and the application is cleaner and easier to test.