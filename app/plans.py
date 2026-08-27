from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanPolicy:
    key: str
    name: str
    document_limit: int | None
    monthly_answer_limit: int | None

    @property
    def managed(self) -> bool:
        return self.document_limit is not None and self.monthly_answer_limit is not None


FREE_POLICY = PlanPolicy(
    key="free",
    name="Free",
    document_limit=5,
    monthly_answer_limit=100,
)
PRO_POLICY = PlanPolicy(
    key="pro",
    name="Pro",
    document_limit=100,
    monthly_answer_limit=2_000,
)
UNMANAGED_POLICY = PlanPolicy(
    key="unmanaged",
    name="Private deployment",
    document_limit=None,
    monthly_answer_limit=None,
)


def policy_for(plan: str | None) -> PlanPolicy:
    if plan == "free":
        return FREE_POLICY
    if plan == "pro":
        return PRO_POLICY
    return UNMANAGED_POLICY


def allowance(used: int, limit: int | None) -> dict[str, int | float | None]:
    if limit is None:
        return {"used": used, "limit": None, "remaining": None, "percent": None}
    return {
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used),
        "percent": round(min(100, used / limit * 100), 1) if limit else 100,
    }
