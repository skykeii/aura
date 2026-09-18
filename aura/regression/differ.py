from typing import Dict, Any, List
from aura.server.db import get_run_details

class RegressionDiffer:
    """Computes before-vs-after regression diffs across step sequences, issue statuses, and metric deltas."""

    def diff_runs(self, run_a_id: str, run_b_id: str) -> Dict[str, Any]:
        run_a = get_run_details(run_a_id)
        run_b = get_run_details(run_b_id)

        if not run_a or not run_b:
            return {"error": "One or both runs not found."}

        # 1. Issue status delta: Fixed, Still Present, Newly Introduced
        findings_a = {f["rule_id"] + "::" + f.get("element", {}).get("name", ""): f for f in run_a.get("findings", [])}
        findings_b = {f["rule_id"] + "::" + f.get("element", {}).get("name", ""): f for f in run_b.get("findings", [])}

        fixed_issues = []
        still_present_issues = []
        newly_introduced_issues = []

        for key, f in findings_a.items():
            if key in findings_b:
                still_present_issues.append(findings_b[key])
            else:
                fixed_issues.append(f)

        for key, f in findings_b.items():
            if key not in findings_a:
                newly_introduced_issues.append(f)

        # 2. Metric Deltas
        metrics_a = run_a.get("metrics", {})
        metrics_b = run_b.get("metrics", {})

        score_delta = metrics_b.get("friction_score", 0) - metrics_a.get("friction_score", 0)
        a11y_delta = metrics_b.get("a11y_score", 0) - metrics_a.get("a11y_score", 0)
        steps_delta = len(run_b.get("steps", [])) - len(run_a.get("steps", []))
        time_delta = round(metrics_b.get("duration_sec", 0) - metrics_a.get("duration_sec", 0), 2)

        # 3. Step Matching for Split-Screen Slider
        matched_steps = []
        steps_a = run_a.get("steps", [])
        steps_b = run_b.get("steps", [])
        max_len = max(len(steps_a), len(steps_b))

        for idx in range(max_len):
            step_a = steps_a[idx] if idx < len(steps_a) else None
            step_b = steps_b[idx] if idx < len(steps_b) else None
            
            matched_steps.append({
                "step_index": idx + 1,
                "step_a": step_a,
                "step_b": step_b,
                "screenshot_a": step_a.get("screenshot_path") if step_a else "",
                "screenshot_b": step_b.get("screenshot_path") if step_b else "",
                "action_a": f"{step_a.get('action_type', '')} #{step_a.get('element_id', '')}" if step_a else "N/A",
                "action_b": f"{step_b.get('action_type', '')} #{step_b.get('element_id', '')}" if step_b else "N/A"
            })

        return {
            "run_a": {
                "id": run_a["id"],
                "goal": run_a["goal"],
                "platform": run_a["platform"],
                "friction_score": metrics_a.get("friction_score", 0),
                "a11y_score": metrics_a.get("a11y_score", 0),
                "total_steps": len(steps_a)
            },
            "run_b": {
                "id": run_b["id"],
                "goal": run_b["goal"],
                "platform": run_b["platform"],
                "friction_score": metrics_b.get("friction_score", 0),
                "a11y_score": metrics_b.get("a11y_score", 0),
                "total_steps": len(steps_b)
            },
            "deltas": {
                "friction_score_delta": score_delta,
                "a11y_score_delta": a11y_delta,
                "steps_delta": steps_delta,
                "time_delta_sec": time_delta
            },
            "issue_breakdown": {
                "fixed_count": len(fixed_issues),
                "still_present_count": len(still_present_issues),
                "newly_introduced_count": len(newly_introduced_issues),
                "fixed": fixed_issues,
                "still_present": still_present_issues,
                "newly_introduced": newly_introduced_issues
            },
            "matched_steps": matched_steps
        }

regression_differ = RegressionDiffer()
