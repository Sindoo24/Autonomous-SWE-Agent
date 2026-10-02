"""Application services: the use cases behind the HTTP API.

Routes parse requests and call these; services hold the rules and call the data layer
(`swe_agent.db.store`) and the agent runtime (`swe_agent.agents.runner`). Services raise the
errors in `swe_agent.core.errors`, which the API maps to HTTP status codes.
"""
