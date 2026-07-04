"""Runtime pieces of the decision service (Dapr Workflow, config client, app).

Kept in a subpackage so importing the pure library (`approvalflow_decision`
top level: models, policy_engine, router) never drags in Dapr or FastAPI.
"""
