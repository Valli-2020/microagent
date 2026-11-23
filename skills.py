"""Skill discovery for micro: <dir>/<name>/SKILL.md with simple frontmatter (stdlib only)."""
import os


def parse_frontmatter(text):
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text
    for i, ln in enumerate(lines[1:], 1):
        if ln.strip() == "---":
            meta = {}
            for l in lines[1:i]:
                k, sep, v = l.partition(":")
                v = v.strip()
                if sep and k.strip() and not k[0].isspace():
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                        v = v[1:-1]
                    meta[k.strip()] = v
            return meta, "\n".join(lines[i + 1:]).lstrip("\n")
    return {}, text  # unterminated block: treat as plain text


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n")


def load(dirs):
    out = {}
    for d in dirs:
        d = os.path.expanduser(str(d))
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            continue
        cands = []
        for e in entries:
            p = os.path.join(d, e, "SKILL.md")
            if os.path.exists(p):
                cands.append((e, p))
            else:  # category folder: <dir>/<category>/<name>/SKILL.md (one level of grouping is allowed)
                try:
                    cands += [(s, os.path.join(d, e, s, "SKILL.md")) for s in sorted(os.listdir(os.path.join(d, e)))]
                except OSError:
                    pass
        for e, p in cands:
            try:
                meta, _ = parse_frontmatter(_read(p))
            except OSError:
                continue
            name = meta.get("name") or e
            if name not in out:
                out[name] = {"description": " ".join(meta.get("description", "").split())[:200], "path": p}
    return out


def body(skills, name):
    s = skills.get(name)
    if not s:
        return f"unknown skill: {name}"
    try:
        return parse_frontmatter(_read(s["path"]))[1][:8000]
    except OSError:
        return f"skill unreadable: {name}"


def selftest():
    import tempfile
    assert parse_frontmatter("hi\n---\na: b") == ({}, "hi\n---\na: b")
    assert parse_frontmatter("---\nname: x\n") == ({}, "---\nname: x\n")
    m, b = parse_frontmatter("---\nname: \"a b\"\ndescription: 'it: works'\nbad line\n---\n\nBody\n---\nmore")
    assert m == {"name": "a b", "description": "it: works"} and b == "Body\n---\nmore", (m, b)
    with tempfile.TemporaryDirectory() as t:
        for d, n, txt in [("a", "one", "---\nname: one\ndescription: " + "x" * 300 + "\n---\nBODY1"),
                          ("b", "one", "---\ndescription: dup\n---\nBODY2"),
                          ("b", "two", "no frontmatter")]:
            os.makedirs(f"{t}/{d}/{n}")
            open(f"{t}/{d}/{n}/SKILL.md", "w").write(txt)
        os.makedirs(f"{t}/b/empty")
        os.symlink(f"{t}/nowhere", f"{t}/b/broken")
        sk = load([f"{t}/a", f"{t}/missing", f"{t}/b"])
        assert set(sk) == {"one", "two"} and len(sk["one"]["description"]) == 200
        assert body(sk, "one") == "BODY1" and body(sk, "two") == "no frontmatter"
        assert "unknown" in body(sk, "nope")
    sk = load([os.path.join(os.path.dirname(os.path.realpath(__file__)), "skills")])
    assert "example" in sk, sk
    print("skills ok;", len(sk), "bundled skills")


if __name__ == "__main__":
    selftest()
