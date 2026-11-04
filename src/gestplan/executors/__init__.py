from __future__ import annotations

from gestplan.executors.agents import IaAgentExec, IaAgentValidatorExec
from gestplan.executors.base import ExecContext, NodeExecutor, Outcome
from gestplan.executors.confluence import ConfluenceCreatePageExec, ConfluenceGetPageExec, ConfluenceUpdatePageExec
from gestplan.executors.flow import (
    LaunchPlansExec,
    ManualApprovalExec,
    MapItemsExec,
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
from gestplan.executors.github_extra import GithubGetFailedLogsExec, GithubMergePrExec
from gestplan.executors.jira import (
    JiraAssignExec,
    JiraCommentExec,
    JiraCreateIssueExec,
    JiraCreateSubtaskExec,
    JiraGetAttachmentsExec,
    JiraGetCommentsExec,
    JiraGetIssueExec,
    JiraSearchExec,
    JiraTransitionExec,
)
from gestplan.executors.local import RunCommandExec
from gestplan.executors.secret_triage import SecretTriageExec
from gestplan.executors.security import SecretScanExec
from gestplan.executors.workspace import CleanupWorkspaceExec, PrepareWorkspaceExec


def default_executors() -> dict[str, NodeExecutor]:
    classes = (
        PrepareWorkspaceExec, CleanupWorkspaceExec, IaAgentExec, IaAgentValidatorExec, RunCommandExec,
        GitCommitExec, GitPushExec, GitCreatePrExec, RouteExec, ManualApprovalExec, PythonScriptExec,
        LaunchPlansExec, WaitPlanExec, JiraGetIssueExec, JiraGetCommentsExec, JiraGetAttachmentsExec,
        JiraAssignExec, JiraCommentExec, JiraTransitionExec, JiraCreateSubtaskExec, ConfluenceCreatePageExec,
        ConfluenceUpdatePageExec, ConfluenceGetPageExec, JiraCreateIssueExec, JiraSearchExec, MapItemsExec,
        GithubGetPrStatusExec, GithubWaitChecksExec, GithubGetReviewCommentsExec, GithubGetWorkflowRunsExec,
        GithubGetFailedLogsExec, GithubMergePrExec, SecretScanExec, SecretTriageExec,
    )
    return {cls.type: cls() for cls in classes}


__all__ = ["ExecContext", "NodeExecutor", "Outcome", "default_executors"]
