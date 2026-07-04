# Verification command (D5) — Windows. Uses the deterministic stub model.
$env:LLM_PROVIDER = "stub"
$env:PYTHONIOENCODING = "utf-8"
python "$PSScriptRoot\verify.py" @args
exit $LASTEXITCODE
