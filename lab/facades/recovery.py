"""Bounded model correction, kept out of the orchestration loop."""

from ..state import RunState


class Recovery:
    max_output_repairs = 2

    def __init__(self, audit):
        self.audit = audit

    def request_correction(self, run: RunState, reason: str) -> None:
        if run.get("output_repairs", 0) >= self.max_output_repairs:
            raise RuntimeError(
                "I could not obtain a valid, verified answer after two recovery attempts. "
                "The invalid answer was withheld; inspect the trace for any API actions already taken."
            )
        run["output_repairs"] = run.get("output_repairs", 0) + 1
        self.audit.record(
            run,
            "repair",
            "Invalid answer rejected; requesting correction",
            {"reason": reason, "attempt": run["output_repairs"]},
        )
        run["messages"].append({"role": "system", "content": reason})
