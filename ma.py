#!/usr/bin/env python3
"""micro: a tiny terminal AI helper for any OpenAI-compatible endpoint. Stdlib only."""
import contextlib, getpass, importlib, json, os, re, shlex, shutil, subprocess, sys, tempfile, time
import urllib.error, urllib.request

DIR = os.path.dirname(os.path.realpath(__file__))  # real repo dir, also when started via the ~/.local/bin symlink
sys.path.insert(0, DIR)


def _imp(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name != name:
            raise
        return None


tui, web, skills = _imp("tui"), _imp("web"), _imp("skills")

HOME_RAW = os.environ.get("MICRO_HOME") or "~/.config/microagent"
HOME = os.path.expanduser(HOME_RAW)
CFG = os.path.join(HOME, "config.json")
DEFAULTS = {"active": "local",
            "models": {"local": {"url": "http://localhost:11434/v1", "model": "qwen3:8b", "key": ""}},
            "crw": {"url": "http://localhost:3002", "key": ""},
            "skills": [os.path.join(HOME_RAW, "skills")],
            "max_turns": 12, "ctx": 8192, "auto": False, "effort": "medium"}
EFFORT = {"low": "Effort: low. Answer fast. Use at most one or two tool calls, only for facts the answer depends on.",
          "medium": "Effort: medium. Check every fact the answer depends on with a tool before you answer. Do not assume.",
          "high": "Effort: high. Check every fact with a tool and confirm important ones a second way (another command or "
                  "another source). Before answering, re-read the tool results and drop any claim they do not support."}
CAP = 4096
STUB = "[old tool result removed to save context]"
# shortcut: regex heuristic only, misses unusual secret formats; upgrade to an entropy check if facts leak keys
SECRET = re.compile(r"sk-[\w-]{16,}|gh[pousr]_\w{20,}|AKIA[0-9A-Z]{16}|xox[abpr]-\w+|-----BEGIN|eyJ[\w-]{10,}\.[\w-]+|"
                    r"\b(password|passwd|passphrase|token|secret|api[_ -]?key)\s*[:=]\s*\S+|\bbearer\s+\S{10,}|"
                    r"[A-Za-z0-9+_-]{32,}", re.I)  # no "/" so long paths pass
HELP = """/settings                change model, endpoint, keys, search, limits (saved at once)
/model                   switch model or add an endpoint
/skills                  list skills
/auto                    toggle running bash without asking for this session (after web results it still asks)
/edit personality|facts  open in $EDITOR
/clear                   start a new conversation
/help                    this list
Ctrl-C stops a reply, Ctrl-D quits"""


class ApiError(Exception):
    pass


# ---- config ----
def save_cfg(cfg):
    fd = os.open(CFG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(CFG, 0o600)  # O_CREAT mode does not touch an existing file


def load_cfg():
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    for n in ("personality.md", "facts.md"):
        src, dst = os.path.join(DIR, n), os.path.join(HOME, n)
        if not os.path.exists(dst) and os.path.exists(src):
            shutil.copy(src, dst)
    if not os.path.exists(CFG):
        save_cfg(DEFAULTS)
    with open(CFG) as f:
        return {**DEFAULTS, **json.load(f)}


def model(cfg):
    m = dict(cfg["models"].get(cfg["active"]) or next(iter(cfg["models"].values())))
    m["key"] = os.environ.get("MICRO_KEY") or m.get("key", "")
    return m


def crw(cfg):
    return {"url": cfg["crw"]["url"], "key": os.environ.get("CRW_KEY") or cfg["crw"].get("key", "")}


def read(name):
    try:
        with open(os.path.join(HOME, name)) as f:
            return f.read().strip()
    except OSError:
        return ""


def load_skills(cfg):
    return skills.load([os.path.expanduser(d) for d in cfg["skills"]])


def system(sk, effort="medium"):
    s = read("personality.md") + "\n\n" + EFFORT.get(effort, EFFORT["medium"]) + "\n\n## Facts\n" + (read("facts.md") or "(none yet)")
    if sk:
        s += "\n\n## Skills (optional)\nNever load a skill on your own. If one clearly fits, offer it in one short sentence and wait for a yes; only then load it with the skill tool.\n" + "\n".join(f"- {n}: {v['description']}" for n, v in sk.items())
    return s


# ---- OpenAI-compatible streaming client ----
def request(m, path, body=None, timeout=300):  # shortcut: one 300 s socket timeout, covers slow model loads; split connect/read if needed
    headers = {"Content-Type": "application/json"}
    if m["key"]:
        headers["Authorization"] = "Bearer " + m["key"]
    req = urllib.request.Request(m["url"].rstrip("/") + path, json.dumps(body).encode() if body else None, headers)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise ApiError(f"{m['url']}: HTTP {e.code} {' '.join(e.read(300).decode(errors='replace').split())}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ApiError(f"{m['url']}: {getattr(e, 'reason', e)}") from None


def sse(lines):
    for raw in lines:
        line = (raw.decode(errors="replace") if isinstance(raw, bytes) else raw).strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        yield json.loads(data)


def assemble(chunks, on_text):
    text, calls = [], {}
    for c in chunks:
        if c.get("error"):
            e = c["error"]
            raise ApiError(str(e.get("message", e) if isinstance(e, dict) else e))
        for ch in c.get("choices") or []:
            d = ch.get("delta") or {}
            if d.get("content"):
                text.append(d["content"])
                on_text(d["content"])
            for tc in d.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "args": ""})
                f = tc.get("function") or {}
                slot["id"] = tc.get("id") or slot["id"]
                slot["name"] = f.get("name") or slot["name"]
                a = f.get("arguments") or ""
                slot["args"] += a if isinstance(a, str) else json.dumps(a)
    out = [calls[k] for k in sorted(calls)]
    for i, c in enumerate(out):
        c["id"] = c["id"] or f"call_{time.time_ns()}_{i}"
    return "".join(text), out


def chat(m, messages, tools, on_text):
    body = {"model": m["model"], "messages": messages, "stream": True}
    if tools:
        body["tools"] = tools
    resp = request(m, "/chat/completions", body)
    try:
        return assemble(sse(resp), on_text)
    except (OSError, ValueError) as e:
        raise ApiError(f"{m['url']}: stream broke: {e}") from None
    finally:
        resp.close()  # also runs on Ctrl-C, so the connection is dropped cleanly


def list_models(m):
    try:
        with request(m, "/models", timeout=5) as r:
            return sorted(x["id"] for x in json.load(r).get("data", []))
    except (ApiError, ValueError, KeyError, TypeError):
        return []


# ---- context ----
def size(msgs):
    return sum(len(str(m.get("content") or "")) + len(json.dumps(m.get("tool_calls", ""))) for m in msgs) // 4


def trim(msgs, ctx):
    # shortcut: chars/4 token estimate, ignores tool schemas; use the server's usage numbers if trimming misfires
    limit = ctx * 0.8
    for m in msgs:
        if size(msgs) <= limit:
            return msgs
        if m["role"] == "tool":
            m["content"] = STUB
    # drop oldest messages, but keep the current chain (last user message onwards) whole
    while size(msgs) > limit and max((i for i, m in enumerate(msgs) if m["role"] == "user"), default=0) > 1:
        msgs.pop(1)
        while msgs[1]["role"] == "tool":  # never leave a tool result without its call
            msgs.pop(1)
    return msgs


# ---- tools ----
def fn(name, desc, arg):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": {arg: {"type": "string"}}, "required": [arg]}}}


TOOLS = [fn("bash", "Run a shell command on this machine", "cmd"), fn("search", "Search the web", "query"),
         fn("scrape", "Read a web page as markdown", "url"), fn("skill", "Load a skill's instructions by name", "name"),
         fn("remember", "Save one durable, non-secret fact about the user or this machine", "fact")]
ARG = {t["function"]["name"]: t["function"]["parameters"]["required"][0] for t in TOOLS}


def append_fact(fact):
    with open(os.path.join(HOME, "facts.md"), "a") as f:
        f.write(f"- {fact}\n")
    return "saved"


def bash(cmd):
    p = subprocess.Popen(cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace", start_new_session=True)
    try:
        return p.communicate(timeout=60)[0].rstrip() + f"\n[exit {p.returncode}]"
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, 9)
        return p.communicate()[0] + "\n[killed after 60 s]"
    except KeyboardInterrupt:
        os.killpg(p.pid, 9)
        raise


def run_tool(name, args, cfg, st):
    if name not in ARG:
        return f"error: unknown tool {name}"
    a = str(args.get(ARG[name], ""))
    # web text is untrusted, so once it is in the conversation every command asks, even with /auto
    ask = name == "remember" or (name == "bash" and not (cfg["auto"] and not st["web"]))
    if not ask:
        tui.tool_line(name, a)  # confirm() shows the line itself
    t = time.time()
    if name == "bash":
        out = bash(a) if not ask or tui.confirm("bash", a) else "user declined to run this command"
    elif name in ("search", "scrape"):
        st["web"] = True
        try:  # web.scrape runs check_url (SSRF block) before calling crw
            out = getattr(web, name)(crw(cfg), a)
        except (ValueError, web.WebError) as e:
            out = f"error: {e}"
    elif name == "skill":
        try:
            out = skills.body(st["skills"], a)
        except (KeyError, OSError, ValueError):
            out = f"error: no skill named {a}"
    else:
        fact = " ".join(a.split())
        if not fact or SECRET.search(fact):
            tui.tool_line(name, "(refused, empty or looks like a secret)")  # never echo the secret
            out = "refused: empty or looks like a secret; secrets are never stored"
        else:
            out = append_fact(fact) if tui.confirm("remember", fact) else "user declined"
    if len(out) > CAP:
        out = out[:CAP] + "\n[truncated]"
    tui.tool_done(f"{len(out.splitlines())} lines · {time.time() - t:.1f} s")
    return out


# ---- agent loop ----
def run_turn(cfg, st, text):
    msgs = st["msgs"]
    st["skills"] = load_skills(cfg)  # re-read so a copied skill folder shows up at once
    msgs[0] = {"role": "system", "content": system(st["skills"], cfg["effort"])}
    msgs.append({"role": "user", "content": text})
    m = model(cfg)
    try:
        for _ in range(cfg["max_turns"]):
            trim(msgs, cfg["ctx"])
            out = tui.Stream()
            with contextlib.ExitStack() as es:
                es.enter_context(tui.spinner())

                def write(tok):
                    es.close()
                    out.write(tok)
                try:
                    reply, calls = chat(m, msgs, TOOLS, write)
                finally:
                    es.close()
                    out.end()
            msg = {"role": "assistant", "content": reply}
            if calls:
                msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": c["args"]}} for c in calls]
            msgs.append(msg)
            if not calls:
                return True
            for c in calls:
                try:
                    args = json.loads(c["args"] or "{}")
                except ValueError:
                    args = None
                res = run_tool(c["name"], args, cfg, st) if isinstance(args, dict) else "error: arguments were not valid JSON"
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": res})
        tui.info(f"stopped after {cfg['max_turns']} turns")
    except KeyboardInterrupt:
        last = next((x for x in reversed(msgs) if x["role"] == "assistant"), {})
        have = {x.get("tool_call_id") for x in msgs}
        msgs += [{"role": "tool", "tool_call_id": c["id"], "content": "aborted by user"}
                 for c in last.get("tool_calls", []) if c["id"] not in have]
        print()
        tui.info("aborted")
    return False


# ---- commands ----
def pick_model(cfg):
    names = list(cfg["models"])
    i = tui.menu("model", [f"{n} · {cfg['models'][n]['model']}" for n in names] + ["+ add"])
    if i is None:
        return
    if i == len(names):
        name, url = tui.ask("name: ").strip(), tui.ask("url: ").strip().rstrip("/")
        if not name or not url:
            return
        key = getpass.getpass("key (enter for none): ").strip()  # no echo, stays out of readline history
        ids = list_models({"url": url, "key": key})
        j = tui.menu("model", ids) if ids else None
        mod = ids[j] if j is not None else tui.ask("model: ").strip()
        if not mod:
            return
        cfg["models"][name] = {"url": url, "model": mod, "key": key}
        names.append(name)
    cfg["active"] = names[i]
    save_cfg(cfg)
    tui.info(f"using {cfg['active']} · {cfg['models'][cfg['active']]['model']}")


def settings(cfg):
    """Popup menu for everything in config.json; each change is saved at once."""
    def num(label, key, lo):
        v = tui.ask(f"{label} [{cfg[key]}]: ").strip()
        if v.isdigit() and int(v) >= lo:
            cfg[key] = int(v)
        elif v:
            tui.error(f"need a whole number >= {lo}")
    def url(label, d, key):
        v = tui.ask(f"{label} [{d.get(key, '')}]: ").strip().rstrip("/")
        if v.startswith(("http://", "https://")):
            d[key] = v
        elif v:
            tui.error("needs to start with http:// or https://")
    def secret(label, d):
        v = getpass.getpass(f"{label} (enter keeps, '-' clears): ").strip()  # no echo, stays out of history
        if v:
            d["key"] = "" if v == "-" else v
    while True:
        name = cfg["active"] if cfg["active"] in cfg["models"] else next(iter(cfg["models"]))
        m, c = cfg["models"][name], cfg["crw"]
        rows = [("model", f"model          {name} · {m['model']}"),
                ("url", f"endpoint       {m['url']}"),
                ("key", f"endpoint key   {'set' if m.get('key') else 'none'}"),
                ("crw", f"search url     {c['url']}"),
                ("crwkey", f"search key     {'set' if c.get('key') else 'none'}"),
                ("skills", f"skill folders  {', '.join(cfg['skills']) or 'none'}"),
                ("turns", f"max tool turns {cfg['max_turns']}"),
                ("ctx", f"context size   {cfg['ctx']}"),
                ("effort", f"effort         {cfg['effort']}"),
                ("auto", f"run bash       {'without asking' if cfg['auto'] else 'ask first'}"),
                ("personality", "edit personality"), ("facts", "edit facts")]
        try:
            i = tui.menu("settings", [r[1] for r in rows])
            if i is None:
                return
            k = rows[i][0]
            if k == "model": pick_model(cfg); continue
            elif k == "url": url("endpoint url", m, "url")
            elif k == "key": secret("endpoint key", m)
            elif k == "crw": url("search url", c, "url")
            elif k == "crwkey": secret("search key", c)
            elif k == "skills":
                v = tui.ask("skill folders, comma separated: ").strip()
                if v: cfg["skills"] = [x.strip() for x in v.split(",") if x.strip()]
            elif k == "turns": num("max tool turns", "max_turns", 1)
            elif k == "ctx": num("context size (tokens)", "ctx", 1024)
            elif k == "effort":
                j = tui.menu("effort", [f"{e:<7}{tui.dim(t.split('. ', 1)[1])}" for e, t in EFFORT.items()])
                if j is not None: cfg["effort"] = list(EFFORT)[j]
            elif k == "auto": cfg["auto"] = not cfg["auto"]
            else: command(cfg, {"msgs": [{}], "web": False}, "/edit " + k); continue
        except (EOFError, KeyboardInterrupt):
            print(); return
        save_cfg(cfg)


def command(cfg, st, line):
    cmd, _, arg = line.partition(" ")
    arg = arg.strip()
    if cmd == "/model":
        pick_model(cfg)
    elif cmd in ("/settings", "/config"):
        settings(cfg)
    elif cmd == "/skills":
        sk = load_skills(cfg)
        tui.info("\n".join(f"{n}  {tui.dim(v['description'])}" for n, v in sk.items()) or "no skills found")
    elif cmd == "/auto":
        cfg["auto"] = not cfg["auto"]  # session only, config.json keeps the default
        tui.info(f"auto {'on: bash runs without asking until web results arrive' if cfg['auto'] else 'off'}")
    elif cmd == "/edit" and arg in ("personality", "facts"):
        ed = os.environ.get("EDITOR") or ("nano" if shutil.which("nano") else "vi")
        try:
            subprocess.call(shlex.split(ed) + [os.path.join(HOME, arg + ".md")])
        except OSError as e:
            tui.error(f"cannot start editor: {e}")
    elif cmd == "/clear":
        del st["msgs"][1:]
        st["web"] = False
        tui.info("new conversation")
    elif cmd == "/help":
        tui.info(HELP)
    else:
        tui.error(f"unknown command {line}, try /help")


def repl(cfg, st):
    while True:
        try:
            line = tui.ask(tui.prompt_str(f"{cfg['active']} · {model(cfg)['model']}")).strip()
            if line.startswith("/"):
                command(cfg, st, line)
            elif line:
                run_turn(cfg, st, line)
                print()
        except KeyboardInterrupt:
            print()
        except EOFError:
            print()
            return
        except ApiError as e:
            tui.error(str(e))


def ping(cfg):
    m, t = model(cfg), time.time()
    reply, _ = chat(m, [{"role": "user", "content": "Reply with the single word: pong"}], None, lambda s: None)
    tui.info(f"ok · {cfg['active']} · {m['model']} · {time.time() - t:.1f} s · {' '.join(reply.split())[:60]!r}")


# ---- self test ----
def selftest():
    raw = [b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n', b"\n", b": keepalive\n",
           b'data: {"choices":[{"delta":{"content":"lo","tool_calls":[{"index":0,"id":"c1","function":{"name":"bash","arguments":"{\\"cmd\\":"}}]}}]}\n',
           b'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"function":{"name":"skill","arguments":{"name":"x"}}}]}}]}\n',
           b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"ls\\"}"}}]}}]}\n',
           b"data: [DONE]\n", b'data: {"choices":[{"delta":{"content":"never"}}]}\n']
    seen = []
    text, calls = assemble(sse(raw), seen.append)
    assert text == "Hello" and seen == ["Hel", "lo"], text
    assert calls[0] == {"id": "c1", "name": "bash", "args": '{"cmd":"ls"}'}, calls
    assert calls[1]["name"] == "skill" and json.loads(calls[1]["args"]) == {"name": "x"} and calls[1]["id"]
    try:
        assemble(sse(['data: {"error":{"message":"model not found"}}']), print)
        assert False
    except ApiError as e:
        assert str(e) == "model not found"
    tc = [{"id": "a", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": tc}, {"role": "tool", "tool_call_id": "a", "content": "x" * 4000},
            {"role": "user", "content": "q2"}]
    assert trim(msgs, 1000)[3]["content"] == STUB and len(msgs) == 5
    msgs[1]["content"] = "y" * 4000
    trim(msgs, 1000)
    assert [m["role"] for m in msgs] == ["system", "assistant", "tool", "user"], msgs
    msgs[3]["content"] = "z" * 4000  # only the oldest pair goes, orphaned tool results go with it
    assert [m["role"] for m in trim(msgs, 1000)] == ["system", "user"], msgs
    msgs = [{"role": "system", "content": "s" * 9000}, {"role": "user", "content": "old"}, {"role": "assistant", "content": "a"},
            {"role": "user", "content": "q"}, {"role": "assistant", "content": "", "tool_calls": tc},
            {"role": "tool", "tool_call_id": "a", "content": "r"}]
    assert [m["role"] for m in trim(msgs, 1000)] == ["system", "user", "assistant", "tool"], msgs
    for s in ("key sk-abcdefghijklmnopqrstuv", "password: hunter22", "token=abc123", "ghp_" + "a" * 36, "x" * 40):
        assert SECRET.search(s), s
    for s in ("User prefers short answers", "The server runs on port 8080", "token budget is 8k", "uses the skills folder", "repo lives in the projects folder"):
        assert not SECRET.search(s), s
    global HOME
    old, HOME = HOME, tempfile.mkdtemp()
    try:
        append_fact("likes tea")
        append_fact("has a Pi 5")
        assert read("facts.md") == "- likes tea\n- has a Pi 5"
    finally:
        shutil.rmtree(HOME)
        HOME = old
    assert "Effort: high" in system({}, "high") and "Effort: medium" in system({}, "bogus")
    print("ma ok")
    for name, mod in (("tui", tui), ("web", web), ("skills", skills)):
        if mod:
            mod.selftest()
        else:
            print(f"{name} missing, skipped")
    return 0


def update(yes=False):
    git = lambda *a: subprocess.run(["git", "-C", DIR, *a], capture_output=True, text=True, timeout=60)
    try:
        old = git("rev-parse", "--short", "HEAD").stdout.strip()
        f = git("fetch", "--tags", "-q") if old else None
        if not old or f.returncode:
            print("micro: cannot check for updates: " + ((f.stderr.strip().splitlines() or ["not a git checkout"])[-1] if f else "not a git checkout")); return 1
        new = git("rev-parse", "--short", "@{u}").stdout.strip()
        if not new:
            print("micro: no upstream branch to update from"); return 1
        if new == old or git("merge-base", "--is-ancestor", "@{u}", "HEAD").returncode == 0:
            print(f"already up to date ({git('describe', '--tags', '--always').stdout.strip()})"); return 0
        print(f"update available: {old} -> {new}\n" + git("log", "--oneline", "HEAD..@{u}").stdout.rstrip()
              + "\n\n" + git("diff", "--stat", "HEAD", "@{u}").stdout.rstrip())
        if not yes:
            if not sys.stdin.isatty():
                print("not applied (no terminal). Run `micro -u -y` to apply."); return 0
            if input("apply? [y/n] ").strip().lower() != "y":
                print("not applied"); return 0
        r = git("merge", "--ff-only", "-q", "@{u}")
    except (OSError, subprocess.TimeoutExpired, EOFError, KeyboardInterrupt) as e:
        print(f"micro: update failed: {e or 'cancelled'}"); return 1
    if r.returncode:
        print("micro: update failed: " + (r.stderr.strip().splitlines() or ["git error"])[-1]); return 1
    print(f"updated {old} -> {new} ({git('describe', '--tags', '--always').stdout.strip()})")
    return 0


def main(argv):
    if argv[:1] == ["--selftest"]:
        return selftest()
    if argv[:1] in (["-v"], ["--version"]):
        r = subprocess.run(["git", "-C", DIR, "describe", "--tags", "--always"], capture_output=True, text=True)
        print("micro " + (r.stdout.strip() or "unknown")); return 0
    if argv[:1] in (["-u"], ["--update"]):
        return update(argv[1:] == ["-y"])
    if argv[:1] and argv[0].startswith("-") and argv[0] != "--ping":
        print('usage: micro ["question"] | -u [-y] | -v | --ping | --selftest')
        return 2
    if not (tui and web and skills):
        sys.exit("micro: tui.py, web.py and skills.py must sit next to ma.py")
    try:
        cfg = load_cfg()
    except (OSError, ValueError) as e:
        sys.exit(f"micro: cannot load {CFG}: {e}")
    st = {"msgs": [{"role": "system", "content": ""}], "web": False, "skills": {}}
    try:
        if argv == ["--ping"]:
            ping(cfg)
            return 0
        if argv:
            return 0 if run_turn(cfg, st, " ".join(argv)) else 1
    except ApiError as e:
        tui.error(str(e))
        return 1
    repl(cfg, st)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
