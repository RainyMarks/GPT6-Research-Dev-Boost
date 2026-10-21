"""PreToolUse guard for Codex: deny commands that change network, proxy or system settings.

AGENTS.md already forbids these changes, but with approval_policy = "never" a rule the
model overlooks is executed straight away. This hook looks at every shell command and
apply_patch call before it runs and denies the clearly dangerous ones, telling the model
why. Reads (netsh ... show, git config --get, Get-NetAdapter) pass. It fails open: input
it cannot parse is let through, so a bug here never blocks normal work.
"""
import sys

# Proxy clients whose processes and config folders are off limits. Add your own here.
PROXY_CLIENTS = ("mihomo", "clash", "verge", "v2ray", "xray", "sing-box", "nekoray", "nekobox", "hiddify")

# Cheap first pass on the raw hook input. Starting Python already costs a few hundred ms on
# some Windows machines, so calls that contain none of these words skip loading json and re.
TRIGGERS = tuple(w.encode() for w in PROXY_CLIENTS) + (
    b"netsh", b"-net", b"dnsclient", b"route", b"ipconfig", b"config", b"npm", b"hosts", b"wsl", b"reg",
    b"itemproperty", b"hk", b"setx", b"environmentvariable", b"service", b"sc ", b"sc.exe", b"net ",
    b"net.exe", b"scheduledtask", b"schtasks", b"executionpolicy", b"mppreference", b"certutil",
    b"certificate", b"bcdedit", b"computer", b"shutdown", b"optionalfeature", b"dism", b"auth.json",
    b".npmrc", b".yarnrc", b".condarc", b".curlrc", b".wgetrc", b"pip.ini", b"pip.conf",
    b" -e ", b" -ec ", b" -en ", b" -enc ", b"encodedcommand")
DELETE_WORDS = (b"remove-item", b"rm ", b"del ", b"erase ", b"rd ", b"rmdir", b"ri ")
RECURSIVE_WORDS = (b"-r", b"-fr", b"/s")

NETWORK = "修改网络或代理配置"
SYSTEM = "修改系统配置"
SECRET = "读取 Codex 凭据"
DELETE = "删除可能指向根目录或用户目录的路径"


def suspicious(raw):
    low = raw.lower()
    if any(t in low for t in TRIGGERS):
        return True
    return any(d in low for d in DELETE_WORDS) and any(r in low for r in RECURSIVE_WORDS)


def compile_rules(re):
    clients = "|".join(re.escape(c) + r"[\w-]*" for c in PROXY_CLIENTS)
    seg = r"[^;|&\n]*"  # rest of the same command segment
    write = (r"(Set-Content|Add-Content|Out-File|Clear-Content|Copy-Item|Move-Item|Remove-Item|New-Item|"
             r"\bsc\b|\bac\b|\bni\b|\bri\b|\bdel\b|\brm\b|\bcp\b|\bmv\b|\btee\b|(?<![\d*])>)")
    hkey = r"(\bHK(LM|CU|CR|U|CC)\b|Registry::)"
    # (category, pattern, extra pattern that must also appear in the command or None)
    shell = [
        (NETWORK, rf"\bnetsh(\.exe)?\b{seg}\b(set|add|delete|reset|import|install|uninstall)\b", None),
        (NETWORK, r"\b(New|Set|Remove|Enable|Disable|Rename|Restart|Reset)-Net[A-Za-z]+\b", None),
        (NETWORK, r"\b(Set|Reset|Add|Remove)-DnsClient[A-Za-z]*\b", None),
        (NETWORK, r"\broute(\.exe)?\s+(add|delete|change|-f)\b", None),
        (NETWORK, r"\bipconfig(\.exe)?\s+/(release|renew)", None),
        (NETWORK, rf"\bgit\s+config\b(?!{seg}--(get|list|show))"
                  rf"(?={seg}\b(https?|core)\.([\w.*:/-]*\.)?(proxy|gitproxy|sslverify|sslcainfo|sslbackend)\b)", None),
        (NETWORK, r"\b(npm|pnpm|yarn)\s+config\s+(set|delete|rm|edit)\b|\bnpm\s+set\s", None),
        (NETWORK, r"\bpip3?\s+config\s+(set|unset|edit)\b", None),
        (NETWORK, rf"\bconda\s+config\b{seg}--(set|add|append|prepend|remove|remove-key)\b", None),
        (NETWORK, rf"\b(Stop-Process|spps|kill|taskkill(\.exe)?)\b{seg}\b({clients})\b", None),
        (NETWORK, rf"\b(Get-Process|gps|ps)\b{seg}\b({clients})\b[^;\n]*\|\s*(Stop-Process|spps|kill)\b", None),
        (NETWORK, rf"[\\/]({clients})[\\/]", write),
        (NETWORK, r"drivers[\\/]+etc[\\/]+hosts\b", write),
        (NETWORK, r"\bwsl(\.exe)?\s+(--shutdown|--terminate|-t\s|--unregister|--manage|--set-default)", None),
        (SYSTEM, r"\breg(\.exe)?\s+(add|delete|import|copy|restore|load|unload)\b", None),
        (SYSTEM, r"\b(Set|New|Remove|Rename|Clear|Copy|Move)-ItemProperty\b", hkey),
        (SYSTEM, rf"\b(Remove-Item|New-Item|ri|ni)\b{seg}{hkey}", None),
        (SYSTEM, r"\bsetx(\.exe)?\b", None),
        (SYSTEM, r"SetEnvironmentVariable\s*\([^)]*\b(User|Machine)\b", None),
        (SYSTEM, r"\b(Stop|Start|Restart|Set|Suspend|New|Remove)-Service\b", None),
        (SYSTEM, r"\bsc(\.exe)?\s+(config|stop|start|delete|create|failure|pause)\s", None),
        (SYSTEM, r"\bnet(\.exe)?\s+(stop|start|user|localgroup|share)\b", None),
        (SYSTEM, r"\b(Register|Unregister|Set|Disable|Enable)-ScheduledTask\b", None),
        (SYSTEM, r"\bschtasks(\.exe)?\s+/(create|delete|change|run|end)\b", None),
        (SYSTEM, rf"\bSet-ExecutionPolicy\b(?!{seg}-Scope\s+Process)", None),
        (SYSTEM, r"\b(Set|Add|Remove)-MpPreference\b", None),
        (SYSTEM, rf"\bcertutil(\.exe)?\b{seg}-(addstore|delstore|importpfx|repairstore)\b", None),
        (SYSTEM, r"\bImport-(Pfx)?Certificate\b", None),
        (SYSTEM, r"\b(bcdedit|Restart-Computer|Stop-Computer)\b|\bshutdown(\.exe)?\s+[/-][srhp]\b", None),
        (SYSTEM, rf"\b(Enable|Disable)-WindowsOptionalFeature\b|\bdism(\.exe)?\b{seg}/(enable|disable)-feature", None),
        (SECRET, r"\.codex[\\/]+auth\.json", None),
    ]
    patch = [
        (NETWORK, r"drivers[\\/]+etc[\\/]+hosts$"),
        (NETWORK, rf"[\\/]({clients})[\\/]"),
        # global (home-level) tool configs; project-level files stay editable
        (NETWORK, r"^(~|[A-Za-z]:[\\/]+Users[\\/]+[^\\/]+|/home/[^/]+|/Users/[^/]+)[\\/]+"
                  r"(\.gitconfig|\.npmrc|\.yarnrc(\.yml)?|\.condarc|\.curlrc|\.wgetrc|\.wslconfig|"
                  r"pip[\\/]+pip\.ini|AppData[\\/]+Roaming[\\/]+pip[\\/]+pip\.ini|\.config[\\/]+pip[\\/]+pip\.conf)$"),
        (SECRET, r"[\\/]\.codex[\\/]+auth\.json$"),
    ]
    return ([(c, re.compile(p, re.I), re.compile(x, re.I) if x else None) for c, p, x in shell],
            [(c, re.compile(p, re.I)) for c, p in patch])


def decode_encoded_commands(re, command):
    """Append the text of any powershell -EncodedCommand payload so the rules can see it."""
    import base64
    extra = []
    for m in re.finditer(r"\b(powershell|pwsh)(\.exe)?\b[^;|&\n]*?\s-(e|ec|en|enc|enco|encod|encode|encoded|"
                         r"encodedcommand)\s+([A-Za-z0-9+/=]{8,})", command, re.I):
        try:
            extra.append(base64.b64decode(m.group(4)).decode("utf-16-le"))
        except (ValueError, UnicodeDecodeError):
            pass
    return "\n".join([command, *extra])


def check_delete(re, command):
    """Recursive deletes of a root or home path, or of a path built from a variable not set here.

    Every Codex command runs in a fresh shell, so a variable not assigned in the same
    command expands to an empty string and "$dir\\*" becomes "\\*", the drive root.
    """
    root = re.compile(r"^([A-Za-z]:)?[\\/]?\*?$|^~[\\/]?\*?$|^\$(HOME|env:USERPROFILE|env:HOMEPATH)[\\/]?\*?$|"
                      r"^[A-Za-z]:[\\/]+Users[\\/]+[^\\/*]+[\\/]?\*?$", re.I)
    for m in re.finditer(r"\b(Remove-Item|ri|rm|del|erase|rd|rmdir)\b(?P<args>[^;|&\n]*)", command, re.I):
        args = m.group("args")
        if not re.search(r"(^|\s)(-r\w*|-fr\w*|--recursive|/s)\b", args, re.I):
            continue
        for token in re.findall(r"\"[^\"]*\"|'[^']*'|\S+", args):
            target = token.strip("\"'")
            if not target or target.startswith("-"):
                continue
            if root.match(target):
                return target
            if token.startswith("'"):  # PowerShell does not expand variables in single quotes
                continue
            for v in re.finditer(r"\$\{?(?P<name>[A-Za-z_][\w:]*)\}?", target):
                name = v.group("name")
                if name.lower() in {"_", "psitem", "home", "pwd", "true", "false", "null"} \
                        or name.lower().startswith("env:") or name.isupper():
                    continue
                n = re.escape(name)
                if not re.search(rf"\${n}\s*=(?!=)|foreach\s*\(\s*\${n}\s+in\b|(^|[\s;('\"]){n}=", command, re.I):
                    return target
    return None


def deny(category, detail):
    import json
    if category == DELETE:
        reason = (f"已拦截：递归删除的目标（{detail[:80]}）可能展开成根目录或用户目录。每条命令都在新的 shell 里运行，"
                  "没在同一条命令里赋值的变量是空的。请写出完整路径，或在同一条命令里赋值、确认非空且位于目标目录内之后再删。")
    else:
        reason = (f"已拦截：这条操作会{category}（{detail[:80]}）。按全局规则，这类操作不由 Codex 执行："
                  "不要换写法重试或绕过；如果任务确实需要，在回复里写明要执行的命令、影响和恢复方法，由用户决定。")
    out = {"systemMessage": f"guard 拦截了一条{category}的操作",
           "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                  "permissionDecisionReason": reason}}
    sys.stdout.write(json.dumps(out, ensure_ascii=True))


def check(raw):
    import json
    import re
    event = json.loads(raw.decode("utf-8"))
    command = (event.get("tool_input") or {}).get("command")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    if not isinstance(command, str):
        return None
    shell_rules, patch_rules = compile_rules(re)
    if event.get("tool_name") == "apply_patch":
        for path in re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", command, re.M):
            for category, pattern in patch_rules:
                if pattern.search(path.strip()):
                    return category, path.strip()
        return None
    text = decode_encoded_commands(re, command.replace("`\r\n", " ").replace("`\n", " "))
    for category, pattern, extra in shell_rules:
        m = pattern.search(text)
        if m and (extra is None or extra.search(text)):
            return category, m.group(0).strip()
    target = check_delete(re, text)
    return (DELETE, target) if target is not None else None


def main():
    try:
        raw = sys.stdin.buffer.read()
        hit = check(raw) if suspicious(raw) else None
    except Exception:  # fail open: never block work because of a bug in this script
        return 0
    if hit:
        deny(*hit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
