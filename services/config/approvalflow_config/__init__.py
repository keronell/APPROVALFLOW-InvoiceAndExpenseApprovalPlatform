"""Config service — serves policy + autonomy thresholds (F7/M13).

Thresholds live in the Dapr state store, so a controller can change them at
runtime (PUT) without a redeploy. Other services read them via Dapr service
invocation.
"""
