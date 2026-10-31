import json

with open("eval/results_20261031_233251.json", "r", encoding="utf-8") as f:
    d = json.load(f)

print(f"Total: {len(d['individual_results'])}")
incorrect = [r for r in d["individual_results"] if r["scoring_status"] == "INCORRECT"]
print(f"Incorrect count: {len(incorrect)}")

for r in incorrect:
    resp = r.get("response") or {}
    mode = resp.get("system_status", {}).get("execution_mode")
    fallback = resp.get("is_fallback_mode")
    abstention = resp.get("is_abstention")
    tokens = resp.get("tokens_used")
    ans = (resp.get("answer") or "")[:90].replace("\n", " ")
    acc_check = r.get("accuracy_check")
    print(f"[{r['id']}] mode={mode} fallback={fallback} abstention={abstention} tokens={tokens}")
    print(f"     expected={r.get('expected_numeric_value')} | actual={acc_check.get('actual')} | check={acc_check.get('status')}")
    print(f"     ans={ans}")
    print("-" * 60)
