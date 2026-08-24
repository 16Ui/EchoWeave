from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class PolicyVerdict(Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class PolicyResult:
    verdict: PolicyVerdict
    reason: str = ""
    reason_code: str = ""
    matched_rules: tuple[str, ...] = field(default_factory=tuple)
    risk_level: str = "low"
    category: str = "unknown"


# 明确拒绝的高危模式（包含任一即拒绝）
_DENIED_RULES: list[tuple[str, str, str]] = [
    ("deny.interactive_editor", r"^\s*(?:vim|vi|nano|emacs|notepad)(?:\s|$)", "interactive editors are not allowed"),
    ("deny.interactive_pager", r"^\s*(?:less|more|man)(?:\s|$)", "interactive pagers are not allowed"),
    ("deny.interactive_shell", r"^\s*(?:bash|sh|zsh|fish|cmd|powershell|pwsh|python|node|irb|pry)\s*$", "interactive shells are not allowed"),
    ("deny.ssh_interactive", r"^\s*ssh(?:\s|$)", "interactive ssh sessions are not allowed"),
    ("deny.rm_recursive_force", r"\brm\s+-[a-z]*r[a-z]*f\b", "destructive recursive file deletion is not allowed"),
    ("deny.rm_force_recursive", r"\brm\s+-[a-z]*f[a-z]*r\b", "destructive recursive file deletion is not allowed"),
    ("deny.shutdown", r"\bshutdown\b", "system shutdown commands are not allowed"),
    ("deny.reboot", r"\breboot\b", "system reboot commands are not allowed"),
    ("deny.mkfs", r"\bmkfs\b", "filesystem formatting commands are not allowed"),
    ("deny.disk_format", r"\bformat\s+[a-z]:", "disk format commands are not allowed"),
    ("deny.git_reset_hard", r"\bgit\s+reset\s+--hard\b", "destructive git reset is not allowed"),
    ("deny.git_clean_force", r"\bgit\s+clean\s+-[a-z]*[xfd]{2,}", "destructive git clean is not allowed"),
    ("deny.git_push_force", r"\bgit\s+push\s+.*--force\b", "force push is not allowed"),
    ("deny.git_push_f", r"\bgit\s+push\s+-f\b", "force push is not allowed"),
    ("deny.del_recursive", r"del\s+/[sf]", "destructive file deletion is not allowed"),
    ("deny.rmdir_recursive", r"rmdir\s+/[sq]", "destructive directory removal is not allowed"),
    ("deny.fork_bomb", r":\(\)\{:\|:&\};:", "fork bomb commands are not allowed"),
    ("deny.dd_disk_write", r"\bdd\s+.*\bof=/dev/", "direct disk write is not allowed"),
    ("deny.chmod_777", r"\bchmod\s+777\b", "world-writable permission change is not allowed"),
    ("deny.curl_pipe_shell", r"\bcurl\b.*\|\s*(?:bash|sh)\b", "piping curl to shell is not allowed"),
    ("deny.wget_pipe_shell", r"\bwget\b.*\|\s*(?:bash|sh)\b", "piping wget to shell is not allowed"),
    ("deny.path_traversal", r"(^|[\\/\s])\.\.([\\/]|$)", "commands may not reference paths outside the workspace"),
    ("deny.windows_absolute_path", r"(^|\s)[a-z]:[\\/]", "commands may not reference absolute paths outside the workspace"),
]

# 需要审批的中风险命令（明确允许前需人工确认）
_APPROVAL_RULES: list[tuple[str, str, str]] = [
    ("approval.git_push", r"\bgit\s+push\b", "git push affects remote repository — approval required"),
    ("approval.git_merge", r"\bgit\s+merge\b", "git merge may cause conflicts — approval required"),
    ("approval.git_rebase", r"\bgit\s+rebase\b", "git rebase rewrites history — approval required"),
    ("approval.git_tag", r"\bgit\s+tag\b", "git tag creates a public ref — approval required"),
    ("approval.pip_install", r"\bpip\s+install\b", "pip install modifies the environment — approval required"),
    ("approval.pip3_install", r"\bpip3\s+install\b", "pip install modifies the environment — approval required"),
    ("approval.npm_install", r"\bnpm\s+install\b", "npm install modifies the environment — approval required"),
    ("approval.npm_publish", r"\bnpm\s+publish\b", "npm publish affects remote registry — approval required"),
    ("approval.python_inline", r"\bpython[^\s]*\s+-c\b", "inline Python can access files and needs approval"),
]

# 明确允许的安全命令（优先于审批规则）
_ALLOWED_RULES: list[tuple[str, str]] = [
    ("allow.python_pytest", r"^\s*python[^\s]*\s+-m\s+pytest\b"),
    ("allow.python_pytest_path", r"^\s*\"?[^\s\"]+python[^\s\"]*\"?\s+-m\s+pytest\b"),
    ("allow.python_script", r"^\s*\"?[^\s\"]+python[^\s\"]*\"?\s+[^\s]+\.py\b"),
    ("allow.pyexe_pytest", r"^\s*\"?[^\s\"]+py\.exe\"?\s+-m\s+pytest\b"),
    ("allow.py_versioned_pytest", r"^\s*\"?[^\s\"]+py\"?\s+-\d+\.\d+\s+-m\s+pytest\b"),
    ("allow.git_readonly", r"^\s*git\s+(?:status|log|diff|show|branch|remote|fetch|stash\s+list|ls-files)\b"),
    ("allow.git_stash_create", r"^\s*git\s+stash\b(?!\s+pop|\s+drop|\s+clear)"),
    ("allow.git_add", r"^\s*git\s+add\b"),
    ("allow.git_commit", r"^\s*git\s+commit\b"),
    ("allow.git_checkout_new_branch", r"^\s*git\s+checkout\s+-b\b"),
    ("allow.git_switch", r"^\s*git\s+switch\b"),
    ("allow.git_clone", r"^\s*git\s+clone\b"),
    ("allow.pip_readonly", r"^\s*\"?[^\s\"]+pip[^\s\"]*\"?\s+(?:list|show|freeze|check)\b"),
    ("allow.pip_install", r"^\s*\"?[^\s\"]+pip[^\s\"]*\"?\s+install\b"),
    ("allow.pip3_install", r"^\s*\"?[^\s\"]+pip3[^\s\"]*\"?\s+install\b"),
    ("allow.python_inline", r"^\s*python[^\s]*\s+-c\b"),
    ("allow.python_inline_path", r"^\s*\"?[^\s\"]+python[^\s\"]*\"?\s+-c\b"),
    ("allow.echo", r"^\s*echo\b"),
    ("allow.cat", r"^\s*cat\b"),
    ("allow.ls", r"^\s*ls\b"),
    ("allow.dir", r"^\s*dir\b"),
    ("allow.pwd", r"^\s*pwd\b"),
    ("allow.which", r"^\s*which\b"),
    ("allow.where", r"^\s*where\b"),
    ("allow.type", r"^\s*type\b"),
    ("allow.npm_safe", r"^\s*npm\s+(?:test|run\s+\S+|ls|list|outdated|audit)\b"),
    ("allow.cargo_safe", r"^\s*cargo\s+(?:test|build|check|clippy|fmt|doc)\b"),
    ("allow.go_safe", r"^\s*go\s+(?:test|build|vet|fmt|doc)\b"),
    ("allow.maven_safe", r"^\s*mvn\s+(?:test|compile|package|verify)\b"),
    ("allow.gradle_safe", r"^\s*gradle\s+(?:test|build|check)\b"),
]

_COMMAND_CATEGORIES: list[tuple[str, str]] = [
    ("navigation", r"^\s*(?:cd|chdir)\b"),
    ("search", r"^\s*(?:rg|grep|find|where|which)\b"),
    ("read", r"^\s*(?:cat|type|ls|dir|pwd|git\s+(?:status|log|diff|show|branch|remote|ls-files))\b"),
    ("test", r"^\s*(?:python[^\s]*\s+-m\s+pytest|\"?[^\s\"]+python[^\s\"]*\"?\s+-m\s+pytest|npm\s+test|npm\s+run|cargo\s+test|go\s+test|mvn\s+test|gradle\s+test)\b"),
    ("build", r"^\s*(?:npm\s+run\s+build|cargo\s+build|go\s+build|mvn\s+(?:compile|package|verify)|gradle\s+build)\b"),
    ("vcs_write", r"^\s*git\s+(?:add|commit|checkout|switch|stash|merge|rebase|tag|push)\b"),
    ("install", r"\b(?:pip|pip3|npm)\s+install\b"),
    ("network", r"^\s*(?:curl|wget|ssh)\b"),
]


@dataclass
class ShellCommandPolicy:
    """集中式 shell 命令策略。

    verdict 说明：
    - ALLOW            — 直接执行
    - DENY             — 立即拒绝，抛 PermissionError
    - REQUIRE_APPROVAL — 需人工确认；非交互模式下可等同 DENY
    """

    auto_approve: bool = False  # True = headless 模式跳过 approval 检查，直接拒绝

    def check(self, command: str) -> PolicyResult:
        normalized = " ".join(command.strip().lower().split())
        category = classify_command(command)

        # 1. 先检查高危拒绝规则（最高优先级）
        for code, pattern, reason in _DENIED_RULES:
            if re.search(pattern, normalized):
                return PolicyResult(
                    verdict=PolicyVerdict.DENY,
                    reason=reason,
                    reason_code=code,
                    matched_rules=(code,),
                    risk_level="critical",
                    category=category,
                )

        # 2. 检查是否匹配允许模式（优先于审批规则）
        for allow_code, allow_pattern in _ALLOWED_RULES:
            if re.search(allow_pattern, normalized):
                # 仍需经过 approval 规则（pip install 被允许但需审批）
                for approval_code, approval_pattern, approval_reason in _APPROVAL_RULES:
                    if re.search(approval_pattern, normalized):
                        if self.auto_approve:
                            return PolicyResult(
                                verdict=PolicyVerdict.DENY,
                                reason=f"{approval_reason} (auto-denied in non-interactive mode)",
                                reason_code=f"{approval_code}.auto_denied",
                                matched_rules=(allow_code, approval_code),
                                risk_level="high",
                                category=category,
                            )
                        return PolicyResult(
                            verdict=PolicyVerdict.REQUIRE_APPROVAL,
                            reason=approval_reason,
                            reason_code=approval_code,
                            matched_rules=(allow_code, approval_code),
                            risk_level="high",
                            category=category,
                        )
                return PolicyResult(
                    verdict=PolicyVerdict.ALLOW,
                    reason_code=allow_code,
                    matched_rules=(allow_code,),
                    risk_level=_risk_for_category(category),
                    category=category,
                )

        # 3. 检查需要审批的规则（未被 allowed 覆盖的中风险命令）
        for approval_code, approval_pattern, approval_reason in _APPROVAL_RULES:
            if re.search(approval_pattern, normalized):
                if self.auto_approve:
                    return PolicyResult(
                        verdict=PolicyVerdict.DENY,
                        reason=f"{approval_reason} (auto-denied in non-interactive mode)",
                        reason_code=f"{approval_code}.auto_denied",
                        matched_rules=(approval_code,),
                        risk_level="high",
                        category=category,
                    )
                return PolicyResult(
                    verdict=PolicyVerdict.REQUIRE_APPROVAL,
                    reason=approval_reason,
                    reason_code=approval_code,
                    matched_rules=(approval_code,),
                    risk_level="high",
                    category=category,
                )

        # 4. 默认拒绝（未在白名单内的命令一律不执行）
        return PolicyResult(
            verdict=PolicyVerdict.DENY,
            reason="command is not in the allowed list; only whitelisted commands are permitted",
            reason_code="deny.not_whitelisted",
            matched_rules=("deny.not_whitelisted",),
            risk_level="medium",
            category=category,
        )

    def validate(self, command: str) -> None:
        """向后兼容接口：ALLOW 直接返回，否则抛 PermissionError。"""
        result = self.check(command)
        if result.verdict == PolicyVerdict.ALLOW:
            return
        raise PermissionError(f"blocked by shell policy: {result.reason}")

    def classify(self, command: str) -> str:
        return classify_command(command)


def classify_command(command: str) -> str:
    normalized = " ".join(command.strip().lower().split())
    for category, pattern in _COMMAND_CATEGORIES:
        if re.search(pattern, normalized):
            return category
    return "unknown"


def _risk_for_category(category: str) -> str:
    if category in {"vcs_write", "install", "network", "unknown"}:
        return "medium"
    return "low"


default_shell_command_policy = ShellCommandPolicy(auto_approve=True)
