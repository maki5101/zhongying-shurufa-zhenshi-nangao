#!/usr/bin/env python3
"""私人信息 / 密钥扫描。提交前（.githooks/pre-commit）和打包后（发布脚本）都跑它，命中就失败。

    privacy_scan.py --staged        扫这次要提交的内容（暂存区里的版本）
    privacy_scan.py --repo          扫 git 会跟踪的全部文件（已跟踪 + 未忽略的新文件）
    privacy_scan.py --history       扫全部历史：所有分支和标签里出现过的每个文件版本，加上提交的作者、
                                    提交者和说明。同样的命中只报一次（附几处位置）
    privacy_scan.py PATH...         扫文件/目录；.apk/.zip/.jar 和 .tar.gz 会解开扫里面
    --lenient                       宽松：所有文件按第三方处理（别人的项目里用，见下）

查什么：
  - 通用（写在这里）：常见密钥格式、/home/<用户>/ 路径、Tailscale 地址段 100.64–127.x.x、真实邮箱
  - 本机（运行时取，不写进任何文件）：$HOME、主机名、git 的 user.email、`tailscale ip -4`
  - 你自己的，一行一个字面串，不分大小写（真名、真实邮箱、手机型号、别的地址）：
      ~/.config/privacy-scan/patterns   全局，所有项目都拦
      <项目>/.private/patterns          只这个项目拦（gitignored）
放行：示例写法 100.x.y.z、alice…@、@example.com、noreply@；或者那一行带 privacy-scan:allow。
公开身份（~/.config/privacy-scan/public，一行一个名字或邮箱）：你对外用的身份，在哪都放行，
  也不会因为它同时是 git 的 user.email 被拦。
宽松模式（--lenient，全局提交钩子在没有自己扫描器的仓库里用）：不是你建的项目，别人的邮箱路径不归你管，
  只查密钥和你的东西（本机身份 + 两份 patterns），等于把所有文件当第三方。
第三方文件（项目根目录的 .privacy-scan-thirdparty，要进 git，一行一个通配符，匹配扫描时显示的路径）：
  只查密钥、本机身份和 .private/patterns，不查"任意邮箱 / 任意 /home/ 路径 / Tailscale 地址段"——
  许可证汇总里的作者邮箱、别人编译服务器的路径、npm 锁文件里的提示语都不是我们的；
  但我们的密钥和真实身份混进去照样拦。
临时排除（项目根目录的 .privacy-scan-ignore，要进 git，一行一条，用 | 分四段）：
    文件通配符 | 命中的原文 | 到期日 YYYY-MM-DD | 原因
  给改不掉、又没法加 privacy-scan:allow 注释的误报用（比如 JSON 里的 128x128@2x.png 被当成邮箱）。
  只对"任意邮箱 / 任意 /home/ 路径 / Tailscale 地址段"生效，密钥、本机身份、patterns 永远不能排除。
  必须写到期日：过期就不再放行并提示；--history 时不再命中任何东西的条目会提示可以删掉。
命中的密钥只显示前 4 位。
原件在 ~/.claude/skills/release-isolation/，每个项目拷一份到 tools/；改规则先改原件再同步各项目。
"""
import datetime
import fnmatch
import io
import os
import re
import socket
import subprocess
import sys
import tarfile
import zipfile

def find_root():
    """项目里的拷贝（tools/privacy_scan.py）属于它所在的仓库；全局原件从哪运行就属于哪个仓库。"""
    here = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    if os.path.exists(os.path.join(here, ".git")):
        return here
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        top = ""
    return top or os.getcwd()


ROOT = find_root()
CONF = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "privacy-scan")
LENIENT = False  # main() 里按 --lenient 设

SECRETS = [  # (名字, 正则)：命中的值打码
    # 放过 sk-ssh-… / sk-ecdsa-…：OpenSSH 硬件密钥的算法名（如 sk-ssh-ed25519-cert-v01），不是密钥
    ("OpenAI/Anthropic 风格 key", rb"\bsk-(?!ssh-|ecdsa-)(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"),
    ("HuggingFace token", rb"\bhf_[A-Za-z0-9]{30,}"),
    ("GitHub token", rb"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    ("AWS key", rb"\bAKIA[0-9A-Z]{16}\b"),
    ("Google API key", rb"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("Slack token", rb"\bxox[abpors]-[A-Za-z0-9-]{10,}"),
    ("私钥", rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("Bearer 口令", rb"\bBearer [A-Za-z0-9._~+/-]{24,}"),
    ("赋了值的 key/token 变量", rb"\b[A-Z][A-Z0-9_]*(?:API_KEY|_TOKEN|_SECRET|PASSWORD)\s*=\s*['\"]?[A-Za-z0-9._~+/-]{12,}"),
]
PERSONAL = [  # 命中原样显示
    ("用户目录路径", rb"/home/[a-z_][a-z0-9_-]*/"),
    ("Tailscale 地址", rb"\b100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}\b"),
    # 放过 git@<主机>：SSH 远程地址的固定写法（git@github.com:owner/repo），不是谁的邮箱
    ("邮箱", rb"\b(?!git@)[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[a-z]{2,}\b"),
]
ALLOW = re.compile(rb"privacy-scan:allow|alice[0-9]*@|@example\.(?:com|org|net)|noreply@|\b100\.x\.y\.z\b")
# 每条规则命中时必然出现的字面片段。整块数据里一个都没有，这条规则就不用跑——结论不变，只是快：
# 逐条正则扫二进制每秒只有几 MB（13 条规则 = 把文件从头扫 13 遍），子串查找快上百倍。
# 新加规则忘了写这里也没事：查不到就照常跑
NEEDLES = {
    "OpenAI/Anthropic 风格 key": (b"sk-",),
    "HuggingFace token": (b"hf_",),
    "GitHub token": (b"ghp_", b"gho_", b"ghu_", b"ghs_", b"ghr_"),
    "AWS key": (b"AKIA",),
    "Google API key": (b"AIza",),
    "Slack token": (b"xoxa-", b"xoxb-", b"xoxp-", b"xoxo-", b"xoxr-", b"xoxs-"),
    "私钥": (b"PRIVATE KEY-----",),
    "Bearer 口令": (b"Bearer ",),
    "赋了值的 key/token 变量": (b"API_KEY", b"_TOKEN", b"_SECRET", b"PASSWORD"),
    "用户目录路径": (b"/home/",),
    "Tailscale 地址": (b"100.",),
    "邮箱": (b"@",),
}


def possible(rules, data):
    """只留下这块数据里可能命中的规则"""
    return [(n, rx) for n, rx in rules if n not in NEEDLES or any(k in data for k in NEEDLES[n])]
# 第三方库里成片的样例邮箱/地址不是我们的：只在我们自己写的文本里查邮箱
EMAIL_ONLY_IN_TEXT = True


def read_list(path):
    if not os.path.exists(path):
        return []
    lines = (line.strip() for line in open(path, encoding="utf-8"))
    return [line for line in lines if line and not line.startswith("#")]


PUBLIC = [p.lower().encode() for p in read_list(os.path.join(CONF, "public"))]


def is_public(b):
    """完全相同才算：用"包含"的话，短名字（比如 maki）会把所有含它的邮箱都放行。"""
    return b.lower() in PUBLIC


def local_literals():
    """本机身份 + 全局和项目的 patterns。只在内存里，不落盘。公开身份不算。"""
    lits = {}
    home = os.path.expanduser("~")
    if home not in ("/", "/root"):
        lits[home.encode() + b"/"] = "本机用户目录"
    host = socket.gethostname()
    if len(host) >= 4 and host != "localhost":
        lits[host.encode()] = "本机主机名"
    for cmd, label in ((["git", "config", "user.email"], "git 提交邮箱"), (["tailscale", "ip", "-4"], "本机 Tailscale IP")):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3, cwd=ROOT).stdout.split()
        except (OSError, subprocess.TimeoutExpired):
            out = []
        for v in out:
            if len(v) >= 6:
                lits[v.encode()] = label
    for v in read_list(os.path.join(CONF, "patterns")):
        lits[v.encode()] = "全局 patterns"
    for v in read_list(os.path.join(ROOT, ".private", "patterns")):
        lits[v.encode()] = ".private/patterns"
    return {k: v for k, v in lits.items() if not is_public(k)}


def thirdparty_patterns():
    """项目声明的第三方文件。这个文件不含私人信息，要进 git。"""
    pf = os.path.join(ROOT, ".privacy-scan-thirdparty")
    if not os.path.exists(pf):
        return []
    lines = (line.strip() for line in open(pf, encoding="utf-8"))
    return [line for line in lines if line and not line.startswith("#")]


THIRDPARTY = thirdparty_patterns()


def load_ignores():
    """临时排除。格式错的行直接报错退出：写错了等于没排除，不能悄悄放过"""
    path = os.path.join(ROOT, ".privacy-scan-ignore")
    entries = []
    if not os.path.exists(path):
        return entries
    today = datetime.date.today()
    for n, raw in enumerate(open(path, encoding="utf-8"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        try:
            glob, text, until, reason = parts
            until = datetime.date.fromisoformat(until)
        except ValueError:
            raise SystemExit(f".privacy-scan-ignore:{n}: 要四段（文件通配符 | 命中的原文 | 到期日 YYYY-MM-DD | 原因）")
        if not (glob and text and reason):
            raise SystemExit(f".privacy-scan-ignore:{n}: 四段都不能空")
        raw_text = text.encode()
        if any(rx.search(raw_text) for _, rx in SECRET_RE) or (LIT_RE and LIT_RE.search(raw_text)):
            # 不回显这一条的内容：它可能就是密钥
            raise SystemExit(f".privacy-scan-ignore:{n}: 这一条是密钥或你的身份信息，不能排除——去改掉源头")
        entries.append({"line": n, "glob": glob, "text": text, "until": until,
                        "expired": until < today, "used": 0})
    return entries


def ignored(name, shown):
    for e in IGNORES:
        if not e["expired"] and e["text"] == shown and fnmatch.fnmatch(name, e["glob"]):
            e["used"] += 1
            return True
    return False


def is_thirdparty(name):
    return any(fnmatch.fnmatch(name, p) for p in THIRDPARTY)


LITS = local_literals()
LIT_RE = re.compile(b"|".join(re.escape(k) for k in sorted(LITS, key=len, reverse=True)), re.I) if LITS else None
LIT_LOWER = [k.lower() for k in LITS]  # 和 LIT_RE 的 re.I 一样只折 ASCII 大小写


def may_have_literal(data):
    """数据里可能有本机/自定义字面串吗（不分大小写）。分块转小写，大文件也不多占一倍内存"""
    if not LIT_LOWER:
        return False
    step, overlap = 64 << 20, max(map(len, LIT_LOWER)) - 1
    for i in range(0, len(data), step):
        chunk = data[i:i + step + overlap].lower()
        if any(k in chunk for k in LIT_LOWER):
            return True
    return False
SECRET_RE = [(n, re.compile(p)) for n, p in SECRETS]
PERSONAL_RE = [(n, re.compile(p)) for n, p in PERSONAL]
IGNORES = load_ignores()


def mask(b):
    s = b.decode("utf-8", "replace")
    return s[:4] + "…" if len(s) > 4 else "…"


def looks_text(data):
    return b"\0" not in data[:4096]


def scan_bytes(name, data, hits, match_name=None):
    """name 是报告里显示的位置；match_name 是拿去匹配第三方 / 临时排除通配符的路径（历史里两者不同）"""
    match_name = match_name or name
    text = looks_text(data)
    # 排除文件里写的就是那些误报原文：通用规则不查它，密钥和身份照查
    thirdparty = LENIENT or is_thirdparty(match_name) or os.path.basename(match_name) == ".privacy-scan-ignore"
    secret_re = possible(SECRET_RE, data)
    personal_re = [] if thirdparty else possible(PERSONAL_RE, data)
    lit_re = LIT_RE if LIT_RE and may_have_literal(data) else None
    if not (secret_re or personal_re or lit_re):
        return  # 一条规则都不可能命中：大图片、PDF、模型多半在这一步就判完了
    lines = data.split(b"\n") if text else [data]
    for i, line in enumerate(lines, 1):
        if ALLOW.search(line):
            continue
        where = f"{name}:{i}" if text else name
        seen = []  # 已报过的区间：同一处被几条规则命中只报一次

        def report(m, label, shown):
            if any(m.start() < e and s < m.end() for s, e in seen):
                return
            if is_public(m.group(0)):  # 你声明过的公开身份
                return
            seen.append((m.start(), m.end()))
            hits.append(f"{where}  [{label}] {shown}")
        for label, rx in secret_re:
            for m in rx.finditer(line):
                report(m, label, mask(m.group(0)))
        if lit_re:  # 本机/自定义的字面串不回显全文（可能就是你的邮箱、IP）
            for m in lit_re.finditer(line):
                report(m, LITS.get(m.group(0)) or LITS.get(m.group(0).lower()) or "本机信息", mask(m.group(0)))
        for label, rx in personal_re:  # 第三方文件 personal_re 是空的：只查上面的密钥和本机身份
            if label == "邮箱" and EMAIL_ONLY_IN_TEXT and not text:
                continue
            for m in rx.finditer(line):
                shown = m.group(0).decode("utf-8", "replace")
                if not ignored(match_name, shown):
                    report(m, label, shown)


def scan_archive(name, data, hits, match_name=None):
    if name.endswith((".apk", ".zip", ".jar", ".aar")):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if not info.is_dir():
                    scan_blob(f"{name}!{info.filename}", z.read(info), hits, f"{match_name or name}!{info.filename}")
        return True
    if name.endswith((".tar.gz", ".tgz", ".tar", ".tar.xz", ".tar.bz2")):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as t:
            for m in t.getmembers():
                if m.isfile():
                    scan_blob(f"{name}!{m.name}", t.extractfile(m).read(), hits, f"{match_name or name}!{m.name}")
        return True
    if name.endswith(".deb"):  # ar 包：debian-binary + control.tar.* + data.tar.*
        if not data.startswith(b"!<arch>\n"):
            raise SystemExit(f"{name}: 不是 ar 格式，没法扫")
        pos = 8
        while pos + 60 <= len(data):
            hdr = data[pos:pos + 60]
            member = hdr[:16].decode().strip().rstrip("/")
            size = int(hdr[48:58].decode().strip())
            body = data[pos + 60:pos + 60 + size]
            if member.endswith(".zst"):
                raise SystemExit(f"{name}!{member}: zstd 压缩，这个扫描器解不开——宁可失败也不跳过")
            scan_blob(f"{name}!{member}", body, hits, f"{match_name or name}!{member}")
            pos += 60 + size + (size & 1)
        return True
    return False


def scan_blob(name, data, hits, match_name=None):
    if not scan_archive(name, data, hits, match_name):
        scan_bytes(name, data, hits, match_name)


def shown_path(p):
    r = os.path.relpath(p)
    return p if r.startswith("..") else r


def scan_path(p, hits):
    if os.path.isdir(p):
        for d, dirs, files in os.walk(p):
            dirs[:] = [x for x in dirs if x != ".git"]
            for f in files:
                fp = os.path.join(d, f)
                if os.path.isfile(fp) and not os.path.islink(fp):
                    scan_blob(shown_path(fp), open(fp, "rb").read(), hits)
    elif os.path.isfile(p):
        scan_blob(shown_path(p), open(p, "rb").read(), hits)
    else:
        raise SystemExit(f"{p}: 不存在——扫不到东西不能算通过")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, cwd=ROOT, check=True).stdout


HISTORY_MAX_BLOB = 200 * 1024 * 1024


def scan_history(hits):
    """所有分支和标签里出现过的每个文件版本（按内容去重），加上每个提交的作者、提交者、说明"""
    paths = {}
    for line in git("rev-list", "--objects", "--all").decode("utf-8", "replace").splitlines():
        sha, _, path = line.partition(" ")
        if path and sha not in paths:
            paths[sha] = path
    if not paths:
        return
    checked = git_input(["cat-file", "--batch-check"], "\n".join(paths) + "\n").decode().splitlines()
    blobs = []
    for row in checked:
        sha, kind, size = row.split()
        if kind != "blob":
            continue
        if int(size) > HISTORY_MAX_BLOB:  # 扫不到不能算通过
            hits.append(f"{paths[sha]}@{sha[:8]}  [太大，没扫] {int(size) // 1048576} MB")
        else:
            blobs.append(sha)
    out = git_input(["cat-file", "--batch"], "\n".join(blobs) + "\n")
    pos = 0
    while pos < len(out):
        header_end = out.index(b"\n", pos)
        sha, _, size = out[pos:header_end].decode().split()
        start = header_end + 1
        data = out[start:start + int(size)]
        pos = start + int(size) + 1
        scan_blob(f"{paths[sha]}@{sha[:8]}", data, hits, paths[sha])
    log = git("log", "--all", "--format=%H%x00%an <%ae>%x00%cn <%ce>%x00%B%x1e")
    for record in log.split(b"\x1e"):
        parts = record.strip(b"\n").split(b"\0")
        if len(parts) < 4:
            continue
        commit = parts[0][:8].decode()
        for label, field in (("作者", parts[1]), ("提交者", parts[2]), ("说明", parts[3])):
            scan_bytes(f"提交 {commit} {label}", field, hits, "<提交信息>")


def git_input(args, text):
    return subprocess.run(["git", *args], input=text.encode(), capture_output=True, cwd=ROOT, check=True).stdout


def summarize(hits, examples=3):
    """历史里同一处信息会在很多版本里重复出现：按"类别 + 内容"合并，附几处位置"""
    groups = {}
    for h in hits:
        where, _, what = h.partition("  ")
        groups.setdefault(what, []).append(where)
    out = []
    for what, wheres in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        more = f" 等 {len(wheres)} 处" if len(wheres) > examples else ""
        out.append(f"{what}   ← {'、'.join(wheres[:examples])}{more}")
    return out


def report_ignores():
    """临时排除的使用情况走 stderr：不影响结果，但每次都看得见"""
    used = [e for e in IGNORES if e["used"]]
    if used:
        first = min(e["until"] for e in used)
        print(f"按 .privacy-scan-ignore 临时放行 {sum(e['used'] for e in used)} 处（最早 {first} 到期）", file=sys.stderr)
    for e in IGNORES:
        if e["expired"]:
            print(f".privacy-scan-ignore:{e['line']} 已于 {e['until']} 到期，不再放行：{e['text']}", file=sys.stderr)


def main(argv):
    global LENIENT
    if "--lenient" in argv:
        LENIENT = True
        argv = [a for a in argv if a != "--lenient"]
    hits = []
    if argv[:1] == ["--staged"]:
        for f in git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split(b"\0"):
            if f:
                scan_blob(f.decode(), git("show", b":" + f), hits)
    elif argv[:1] == ["--history"]:
        scan_history(hits)
        hits = summarize(hits)
    elif argv[:1] == ["--repo"]:
        for f in git("ls-files", "-co", "--exclude-standard", "-z").split(b"\0"):
            fp = os.path.join(ROOT, f.decode())
            if f and os.path.isfile(fp) and not os.path.islink(fp):
                scan_blob(f.decode(), open(fp, "rb").read(), hits)
    elif argv:
        for p in argv:
            scan_path(p, hits)
    else:
        print(__doc__)
        return 2
    report_ignores()
    if argv[:1] == ["--history"]:  # 只有扫遍历史（含当前文件）才能断定"不再命中"：有的条目只为历史里的旧版本存在
        for e in IGNORES:
            if not e["expired"] and not e["used"]:
                print(f".privacy-scan-ignore:{e['line']} 已经不命中任何东西，可以删掉：{e['text']}", file=sys.stderr)
    if hits:
        print("发现私人信息或密钥：")
        for h in hits:
            print("  " + h)
        if LENIENT:
            print("这是别人的项目，只拦了你的密钥和你的身份信息。确实要提交：git commit --no-verify")
        elif argv[:1] == ["--history"]:
            print("这些在 git 历史里，改现在的文件去不掉。密钥先去服务商吊销换新；"
                  "然后重写历史（release-isolation skill 的「历史里有私人信息」）。"
                  "查哪些提交带着某个文件版本：git log --all --find-object=<@ 后面那段>")
        else:
            print("改掉，或挪到 .private/（不进 git）。确实是示例的那一行加 privacy-scan:allow；"
                  "改不了又没法加注释的误报，写进 .privacy-scan-ignore（带到期日）。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
