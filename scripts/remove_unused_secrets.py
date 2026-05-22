"""Remove unused thinker model override keys from secrets JSON file."""

import json
import sys
from pathlib import Path

KEYS_TO_REMOVE = [
    "COMMUNITY_THINKER_MODEL",
    "COMMUNITY_THINKER_REASONING_EFFORT",
    "COMMUNITY_THINKER_VERBOSITY",
    "FACILITIES_THINKER_MODEL",
    "FACILITIES_THINKER_REASONING_EFFORT",
    "FACILITIES_THINKER_VERBOSITY",
    "GUEST_PARKING_THINKER_MODEL",
    "GUEST_PARKING_THINKER_REASONING_EFFORT",
    "GUEST_PARKING_THINKER_VERBOSITY",
    "HANDOFF_TO_HUMAN_THINKER_MODEL",
    "HANDOFF_TO_HUMAN_THINKER_REASONING_EFFORT",
    "HANDOFF_TO_HUMAN_THINKER_VERBOSITY",
    "PACKAGES_THINKER_MODEL",
    "PACKAGES_THINKER_REASONING_EFFORT",
    "PACKAGES_THINKER_VERBOSITY",
    "POLICY_AND_LEDGER_THINKER_MODEL",
    "POLICY_AND_LEDGER_THINKER_REASONING_EFFORT",
    "POLICY_AND_LEDGER_THINKER_VERBOSITY",
    "QNA_THINKER_MODEL",
    "QNA_THINKER_REASONING_EFFORT",
    "QNA_THINKER_VERBOSITY",
    # Legacy/Dead keys
    "AUTH_CLIENT_ID",
    "AUTH_CLIENT_SECRET",
    "AUTH_SCOPES",
    "AUTH_TOKEN_ENDPOINT",
    "TRACE_SESSION",
    "TEMPORAL_API_KEY",
    "AI_CONFIG_DATABASE_URL",
    "ASPIRE_DASHBOARD_ENDPOINT",
    "MCP_CONNECTION_POOLING_ENABLED",
    "LDP_PREFETCH_PROPERTY_IDS",
    "KAFKA_PROPERTY_INFO_CONSUMER_ENABLED",
    "KAFKA_PROPERTY_INFO_CONSUMER_GROUP_ID",
    "KAFKA_PROPERTY_INFO_CONSUMER_SOURCE_TOPIC",
    # LDP Cache Warming keys
    "LDP_CACHE_WARMING_ENABLED",
    "LDP_CACHE_WARMING_DELAY_BETWEEN_PROPERTIES_SECONDS",
    "LDP_CACHE_WARMING_EVICTION_HOURS",
    # Not in codebase (no settings.py field, no code reference)
    "REALTIME_INPUT_AUDIO_NOISE_REDUCTION_ENABLED",
    "FLAG_USE_NEW_SMS_UPDATE_CONSENT_TOOL",
    "REDIS_URL",
    # Alpha-only, matches settings.py default
    "FACILITIES_MCP_AUTH_ENABLED",
    # Redundant: same value in all envs AND matches settings.py default
    # -- Model settings --
    "MODEL",
    "MODEL_REASONING_EFFORT",
    "RESIDENT_ONE_MODEL_REASONING_EFFORT",
    "RESIDENT_ONE_MODEL_VERBOSITY",
    # -- MCP auth (same as code defaults) --
    "BOOKS_AUTH_CLIENT_ID",
    "BOOKS_AUTH_ENABLED",
    "BOOKS_AUTH_SCOPES",
    "KNOCK_MCP_AUTH_CLIENT_ID",
    "KNOCK_MCP_AUTH_SCOPES",
    "LOFT_MCP_AUTH_ENABLED",
    "ONESITE_MCP_AUTH_SCOPES",
    # -- Cache / expiration --
    "EXPIRE_CHAT",
    "EXPIRE_DEFAULT",
    "EXPIRE_SMS",
    "EXPIRE_VOICE",
    "LDP_CACHE_EARLY_TTL",
    "LDP_CACHE_TTL",
    "STREAMING_HEARTBEAT_INTERVAL",
    # -- Feature flags (all match defaults) --
    "ANYIO_PATCH_PRESERVE_TASKS_ENABLED",
    "INTERRUPT_SUPPRESSION_ENABLED",
    "FACILITIES_THINKER_SELF_SERVICE_ENABLED",
    "FILLER_ESCALATION_ENABLED",
    "FILLER_ESCALATION_THRESHOLD",
    "FILLER_PHRASES",
    "IDENTITY_VERIFICATION_ENABLED",
    "PREAMBLE_SPEECH_DETECTION_ENABLED",
    "THINKER_CONCURRENCY_GUARD_ENABLED",
    # -- Voice / audio --
    "MAX_PLAYBACK_ATTEMPTS",
    "OPENAI_AUDIO_FORMAT",
    "PLAYBACK_END_TIMEOUT_SECONDS",
    "REALTIME_LANGUAGE_CLASSIFICATION_MAX_CONCURRENCY",
    # -- Observability --
    "LANGSMITH_ENDPOINT",
    "LOG_LEVEL",
    # -- Misc --
    "INSIGHT_NEWS_CHANNELS",
    "INSIGHT_NEWS_ITEMS",
    "REDIS_PORT",
]

if len(sys.argv) < 2:
    print(f"Usage: python {sys.argv[0]} <input.json> [output.json]")
    sys.exit(1)

input_file = Path(sys.argv[1])
output_file = Path(sys.argv[2]) if len(sys.argv) > 2 else input_file.with_stem(input_file.stem + "-cleaned")

secrets = json.loads(input_file.read_text())

removed = []
not_found = []
for key in KEYS_TO_REMOVE:
    if key in secrets:
        del secrets[key]
        removed.append(key)
    else:
        not_found.append(key)

output_file.write_text(json.dumps(secrets, indent=2) + "\n")

print(f"Removed {len(removed)} keys, wrote to {output_file}")
if not_found:
    print(f"Keys not found in file: {not_found}")
