from __future__ import annotations

from gestplan.executors.agents import IaAgentExec, IaAgentValidatorExec
from gestplan.executors.base import ExecContext, NodeExecutor, Outcome
from gestplan.executors.confluence import ConfluenceCreatePageExec
from gestplan.executors.flow import (
    LaunchPlansExec,
    ManualApprovalExec,
    PythonScriptExec,
    RouteExec,
    WaitPlanExec,
)
from gestplan.executors.git import GitCommitExec, GitCreatePrExec, GitPushExec
from gestplan.executors.github import (
    GithubGetPrStatusExec,
    GithubGetReviewCommentsExec,
    GithubGetWorkflowRunsExec,
    GithubWaitChecksExec,
)
from gestplan.executors.jira import (
    JiraAssignExec,
    JiraCommentExec,
    JiraCreateSubtaskExec,
    JiraGetAttachmentsExec,
    JiraGetCommentsExec,
    JiraGetIssueExec,
    JiraTransitionExec,
)
from gestplan.executors.local import RunCommandExec
from gestplan.executors.security import SecretScanExec
from gestplan.executors.workspace import CleanupWorkspaceExec, PrepareWorkspaceExec


def default_executors() -> dict[str, NodeExecutor]:
    classes = (
        PrepareWorkspaceExec, CleanupWorkspaceExec, IaAgentExec, IaAgentValidatorExec, RunCommandExec,
        GitCommitExec, GitPushExec, GitCreatePrExec, RouteExec, ManualApprovalExec, PythonScriptExec,
        LaunchPlansExec, WaitPlanExec, JiraGetIssueExec, JiraGetCommentsExec, JiraGetAttachmentsExec,
        JiraAssignExec, JiraCommentExec, JiraTransitionExec, JiraCreateSubtaskExec, ConfluenceCreatePageExec,
        GithubGetPrStatusExec, GithubWaitChecksExec, GithubGetReviewCommentsExec, GithubGetWorkflowRunsExec,
        SecretScanExec,
    )
    return {cls.type: cls() for cls in classes}


__all__ = ["ExecContext", "NodeExecutor", "Outcome", "default_executors"]
