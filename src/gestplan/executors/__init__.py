from __future__ import annotations

from gestplan.executors.agents import IaAgentExec, IaAgentValidatorExec
from gestplan.executors.base import ExecContext, NodeExecutor, Outcome
from gestplan.executors.flow import (
    LaunchPlansExec,
    ManualApprovalExec,
    PythonScriptExec,
    RouteExec,
    WaitPlanExec,
)
from gestplan.executors.git import GitCommitExec, GitCreatePrExec, GitPushExec
from gestplan.executors.local import RunCommandExec
from gestplan.executors.workspace import CleanupWorkspaceExec, PrepareWorkspaceExec


def default_executors() -> dict[str, NodeExecutor]:
    classes = (
        PrepareWorkspaceExec, CleanupWorkspaceExec, IaAgentExec, IaAgentValidatorExec, RunCommandExec,
        GitCommitExec, GitPushExec, GitCreatePrExec, RouteExec, ManualApprovalExec, PythonScriptExec,
        LaunchPlansExec, WaitPlanExec,
    )
    return {cls.type: cls() for cls in classes}


__all__ = ["ExecContext", "NodeExecutor", "Outcome", "default_executors"]
