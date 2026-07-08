"""Command guard for the Docker sandbox shell.

Every shell command the agent asks to run eventually reaches
``DockerSandbox.execute()``, which does ``container.exec_run(["sh", "-c",
command])``.  Because the command string originates from LLM output that has
been influenced by untrusted Jira/GitHub issue text, it must be treated as
untrusted.  This module classifies a command as **allow** or **block** using
**only the Python standard library** (``re`` + ``shlex``).

Design goals
------------
* **Layered, not just a regex blocklist.**  We split the command on shell
  operators (``;``, ``&&``, ``||``, ``|``) so an attacker cannot smuggle a
  destructive payload past a benign prefix (``ls; rm -rf /``).
* **Fail closed.**  Anything we cannot confidently tokenise (e.g. genuinely
  malformed shlex input) is treated as a parse error and *blocked* with a
  clear reason, never silently allowed.
* **Low false-positive rate for normal dev work.**  ``git``, ``gh``, ``npm``,
  ``python``, ``cat``, ``grep``, ``ls``, ``make``, ``pytest`` etc. are all
  allowed by default; only genuinely destructive or exfiltration-class
  commands are blocked.
* **Readable reasons.**  Each block decision carries a human/LLM-readable
  ``reason`` and the ``pattern`` that triggered it, so the agent can
  self-correct.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "BLOCKED_COMMAND_MESSAGE",
    "CommandDecision",
    "CommandGuard",
    "evaluate_command",
]

# Message prepended to blocked tool results so the LLM understands why its
# command did not run and how to recover.
BLOCKED_COMMAND_MESSAGE = (
    "Command blocked by security middleware. "
    "If this is a legitimate operation, use a safer alternative and retry."
)


# ---------------------------------------------------------------------------
# 1. Destructive / system-bricking patterns
# ---------------------------------------------------------------------------
#
# Each entry: (compiled_regex, short_reason).  Matched against the *whole*
# command string as well as against each individual sub-command, so chaining
# cannot bypass detection.

_DESTRUCTIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Recursive force-delete of root or home
    (re.compile(r"\brm\s+(?:-[a-zA-Z]*r[a-zA-Z]*f?|-[a-zA-Z]*f[a-zA-Z]*r?)\s+(?:--no-preserve-root\s+)?(?:/(?:\s|$|/|\*))"), "recursive delete of root filesystem"),
    (re.compile(r"\brm\s+(?:-[a-zA-Z]*r[a-zA-Z]*f?)\s+(?:~|/root|/home|/usr|/var|/bin|/sbin|/boot|/etc)(?:\s|$|/)"), "recursive delete of system directory"),
    (re.compile(r"\brm\s+--no-preserve-root\b"), "bypass of rm root protection"),
    # Fork bomb:  :(){ :|:& };:   and variants
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    (re.compile(r"\bbash\s+-c\s+['\"]?:\(\)"), "fork bomb via bash -c"),
    # Overwrite / wipe block devices
    (re.compile(r"\bdd\b[^|;\n]*\bof\s*=\s*/dev/(?:sd[a-z]+|nvme\d+n\d+|hd[a-z]+|vd[a-z]+|disk\d+|mmcblk\d+)"), "raw write to block device"),
    (re.compile(r"\bmkfs(?:\.\w+)?\b\s+/dev/"), "filesystem format on block device"),
    (re.compile(r"\bshred\b[^|;\n]*\s+/dev/"), "shred of block device"),
    # Halt / reboot / poweroff / init
    (re.compile(r"\b(shutdown|reboot|poweroff|halt)\b"), "system shutdown / reboot"),
    (re.compile(r"\binit\s+0\b"), "init 0 (shutdown)"),
    (re.compile(r"\b(systemctl|service)\s+(?:stop|restart|disable|mask)\b"), "system service disruption"),
    # Killing PID 1 / the container init
    (re.compile(r"\bkill(?:\s+-\d+)?\s+(?:-?1\b|0\b|pidof\s+\w+|init\b)"), "kill of init / process group"),
    (re.compile(r"\bkill(?:all|pkill)\b\s+-9\b"), "forceful killall"),
    # Writing to kernel / boot / proc-sys tunables
    (re.compile(r">\s*/boot/"), "write to /boot"),
    (re.compile(r">\s*/proc/sys/"), "write to /proc/sys (kernel tunable)"),
    (re.compile(r"\bsysctl\b\s+-w\b"), "runtime kernel parameter change"),
    # Remount / filesystem mount changes
    (re.compile(r"\bmount\b\s+-o\s+remount"), "remount filesystem"),
    (re.compile(r"\bumount\b\s+/"), "unmount filesystem"),
    # iptables / network stack reconfiguration
    (re.compile(r"\biptables\b"), "iptables firewall change"),
    (re.compile(r"\b(ip\s+route|route\s+(?:add|del))\b"), "routing table change"),
)


# ---------------------------------------------------------------------------
# 2. Privilege escalation / persistence
# ---------------------------------------------------------------------------

_PRIVILEGE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsudo\b"), "sudo privilege escalation"),
    (re.compile(r"\bsu\s+(?:root|[-]\s*root)?\b"), "switch to root user"),
    (re.compile(r"\bchmod\s+[0-7]?777\b\s+/"), "world-writable permission on system path"),
    (re.compile(r"\bchown\s+(?:-R\s+)?root\b"), "chown to root"),
    (re.compile(r"\bchroot\b"), "chroot escape attempt"),
    (re.compile(r"\bcapsh\b"), "linux capability manipulation"),
    (re.compile(r"\bsetcap\b"), "set file capabilities"),
    (re.compile(r"\bnsenter\b"), "namespace escape"),
    (re.compile(r"\bunshare\b"), "namespace creation"),
    # Crontab / persistence
    (re.compile(r"\b(crontab|crond)\b"), "cron persistence"),
    (re.compile(r">\s*/etc/cron"), "write to cron directory"),
)


# ---------------------------------------------------------------------------
# 3. Remote-code-execution / reverse-shell payloads
# ---------------------------------------------------------------------------

_REMOTE_EXEC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # curl/wget piped to an interpreter
    (re.compile(r"\b(curl|wget|fetch)\b[^|;\n]{0,200}?\|\s*(sh|bash|zsh|dash|python\d?|perl|ruby|node)\b"), "pipe remote content to shell interpreter"),
    # curl ... | sh even when split via process substitution
    (re.compile(r"\b(curl|wget)\b[^|;\n]{0,200}?<\s*\(\s*(sh|bash)\s*\)"), "process-substitution shell exec of remote content"),
    # bash <(curl ...) style
    (re.compile(r"\b(bash|sh|zsh)\b\s+<\s*\(\s*(curl|wget)\b"), "shell reads script from remote fetch"),
    # Reverse-shell classics
    (re.compile(r"\bnc\b[^|;\n]{0,80}?\s+-e\b"), "netcat reverse shell (-e)"),
    (re.compile(r"\bncat\b[^|;\n]{0,80}?\s+-e\b"), "ncat reverse shell (-e)"),
    (re.compile(r"\bbash\b[^|;\n]{0,80}?>&\s*/dev/tcp/"), "bash /dev/tcp reverse shell"),
    (re.compile(r"\bpython\d?\b[^|;\n]{0,120}?socket\.socket\b"), "python reverse shell via socket"),
    (re.compile(r"\bperl\b[^|;\n]{0,120}?socket\b"), "perl reverse shell via socket"),
    # Download-then-execute two-step obfuscation: curl -o /tmp/x ...; bash /tmp/x
    (re.compile(r"\b(curl|wget)\b[^|;\n]{0,120}?\s+-o\s+\S+\s+\S+[^|;\n]{0,40}?;\s*(sh|bash)\s+\S+"), "download-then-execute remote script"),
)


# ---------------------------------------------------------------------------
# 4. Secret / credential exfiltration
# ---------------------------------------------------------------------------

_EXFIL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bprintenv\b"), "print environment variables (potential secret leak)"),
    # Bare ``env`` (no arguments) dumps every environment variable to stdout,
    # which leaks secrets such as GITHUB_TOKEN.  ``env VAR=x cmd`` (setting a
    # variable before a command) is legitimate, so we only block the bare form.
    # Anchored to the start of a sub-command so a filename like ``.env`` or an
    # argument like ``--env`` is NOT matched.
    (re.compile(r"^\s*env\s*$"), "dump environment variables (potential secret leak)"),
    (re.compile(r"\bexport\b[^|;\n]{0,5}$"), "bare export (dumps environment)"),
    (re.compile(r"\bcat\s+/proc/\d+/environ\b"), "read process environment (secret leak)"),
    (re.compile(r"\b(echo|printf|cat)\b[^|;\n]{0,40}?\$\{?(GITHUB_TOKEN|GH_TOKEN|SECRET|API_KEY|ACCESS_TOKEN|PASSWORD|PRIVATE_KEY)\}?"), "echo credential value"),
    (re.compile(r"\b(echo|printf|cat)\b[^|;\n]{0,40}?\$\{?(AWS_SECRET_ACCESS_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)\}?"), "echo cloud/AI credential value"),
    (re.compile(r"\bcat\s+~?/\.ssh/(id_rsa|id_ed25519|id_ecdsa)\b"), "read private SSH key"),
    (re.compile(r"\bcat\s+~?/\.netrc\b"), "read .netrc credentials"),
    (re.compile(r"\bcat\s+/etc/shadow\b"), "read /etc/shadow"),
    # Posting secrets to a network endpoint
    (re.compile(r"\b(curl|wget)\b[^|;\n]{0,120}?\$\{?(GITHUB_TOKEN|GH_TOKEN|SECRET|API_KEY|ACCESS_TOKEN|PRIVATE_KEY)\}?"), "send credential to remote endpoint"),
)


# ---------------------------------------------------------------------------
# 5. Network exfiltration of source / arbitrary data
# ---------------------------------------------------------------------------

_NETWORK_EXFIL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # curl/wGET POST of a file to an arbitrary host (not github.com / the proxy).
    # Note: no ``\b`` before the flag group -- hyphens are not word characters,
    # so ``\b-`` never matches.  The ``--``/``-`` literal prefix is enough.
    (re.compile(r"\b(curl|wget)\b[^|;\n]{0,40}?(?:--data(?:-binary)?|-d|-T|--upload-file)[^|;\n]{0,120}?@?/"), "upload local file to remote host"),
    # tar/gzip piped over the network
    (re.compile(r"\btar\b[^|;\n]{0,80}?\|\s*(nc|ncat|curl|wget)\b"), "stream archive to network endpoint"),
)


# All rule groups, in evaluation order.  Earlier groups take precedence for
# the reported reason.
_ALL_PATTERN_GROUPS: tuple[tuple[str, tuple[tuple[re.Pattern[str], str], ...]], ...] = (
    ("destructive", _DESTRUCTIVE_PATTERNS),
    ("privilege_escalation", _PRIVILEGE_PATTERNS),
    ("remote_execution", _REMOTE_EXEC_PATTERNS),
    ("secret_exfiltration", _EXFIL_PATTERNS),
    ("network_exfiltration", _NETWORK_EXFIL_PATTERNS),
)


# Shell operators that separate sub-commands.  We split on these so that a
# destructive tail (``ls && rm -rf /``) is still inspected independently.
#
# Note: ``|`` is both a pipe and a logical-or context separator.  Splitting on
# it is safe for our purposes because a *piped* destructive command is still
# destructive (e.g. ``rm -rf / | tee log`` still deletes).  Sub-commands that
# are merely receiving piped *input* (``cat foo | grep x``) won't match any
# blocklist pattern, so the split is conservative-correct here.
_SUBCOMMAND_SPLIT_RE = re.compile(r"(?:;|&&|\|\||\|)")


@dataclass
class CommandDecision:
    """The verdict returned by :func:`evaluate_command`.

    Attributes:
        action: Either ``"allow"`` or ``"block"``.
        reason: Human-readable explanation.  Empty when allowed.
        category: Rule group that fired (e.g. ``"destructive"``).  Empty when
            allowed.
        pattern: The matching rule text, for diagnostics.  Empty when allowed.
        sub_commands: The list of sub-commands the original command was split
            into, for transparency / logging.
    """

    action: Literal["allow", "block"]
    reason: str = ""
    category: str = ""
    pattern: str = ""
    sub_commands: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    @property
    def blocked(self) -> bool:
        return self.action == "block"


def _split_sub_commands(command: str) -> list[str]:
    """Split *command* into its constituent sub-commands on shell operators.

    Returns at least one element (the original string if no operator is found).
    """
    parts = _SUBCOMMAND_SPLIT_RE.split(command)
    return [part.strip() for part in parts if part and part.strip()]


def _match_command(command: str) -> tuple[str, str, str] | None:
    """Run every pattern group against *command*.

    Returns ``(category, pattern_description, reason)`` for the first match, or
    ``None`` if nothing matched.
    """
    for category, patterns in _ALL_PATTERN_GROUPS:
        for pattern, reason in patterns:
            if pattern.search(command):
                return category, reason, reason
    return None


class CommandGuard:
    """Stateless classifier for sandbox shell commands.

    Encapsulated as a class so callers (the middleware, the Docker
    integration, tests) share one well-defined entry point and so the rule set
    can be extended/subclassed without touching call sites.
    """

    def evaluate(self, command: str) -> CommandDecision:
        """Classify *command* as allow/block.

        Strategy:
          1. Refuse to even parse malformed shlex input (fail closed).
          2. Split on shell operators and inspect every sub-command, so a
             destructive tail cannot hide behind a benign prefix.
          3. Match each sub-command (and the whole string) against every rule
             group; the first hit wins.
        """
        if not isinstance(command, str) or not command.strip():
            return CommandDecision(
                action="block",
                reason="Empty command.",
                category="malformed",
                pattern="empty",
                sub_commands=[command if isinstance(command, str) else ""],
            )

        # Fail closed on unparseable shell input.  shlex.split raises
        # ValueError on unbalanced quotes; we treat that as suspicious rather
        # than passing the raw string to the shell.
        try:
            shlex.split(command, comments=True, posix=True)
        except ValueError as exc:
            return CommandDecision(
                action="block",
                reason=f"Unparseable shell command ({exc}).",
                category="malformed",
                pattern="shlex_parse_error",
                sub_commands=[command],
            )

        sub_commands = _split_sub_commands(command)

        # Inspect the whole command first (catches cross-token payloads that
        # splitting might obscure), then each sub-command.
        candidates: list[str] = [command, *sub_commands]
        for candidate in candidates:
            match = _match_command(candidate)
            if match is not None:
                category, pattern_desc, reason = match
                return CommandDecision(
                    action="block",
                    reason=reason,
                    category=category,
                    pattern=pattern_desc,
                    sub_commands=sub_commands,
                )

        return CommandDecision(
            action="allow",
            sub_commands=sub_commands,
        )


# Module-level singleton + convenience function, mirroring the style of the
# input sanitizer so call sites read ``evaluate_command(cmd)``.
_DEFAULT_GUARD = CommandGuard()


def evaluate_command(command: str) -> CommandDecision:
    """Classify *command* using the default :class:`CommandGuard`."""
    return _DEFAULT_GUARD.evaluate(command)
