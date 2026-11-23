"""Terminal UI for micro: one colour token set, streaming markdown, menu, prompt. Stdlib only."""
import contextlib, io, os, re, select, shutil, sys, threading, time

# Token set: name -> (truecolor rgb, 256-colour fallback)
TOKENS = {"acc": ((127, 183, 190), 109), "dim": ((125, 130, 135), 244),
          "warn": ((214, 168, 92), 179), "err": ((200, 100, 100), 167)}
RESET, BOLD = "\x1b[0m", "\x1b[1m"
_force = None  # selftest hook: True/False overrides TTY detection


def _on():
    if _force is not None:
        return _force
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _fg(name):
    if not _on():
        return ""
    rgb, n = TOKENS[name]
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return "\x1b[38;2;%d;%d;%dm" % rgb
    return "\x1b[38;5;%dm" % n


def _rs(): return RESET if _on() else ""


def _paint(name, s):
    return _fg(name) + s + RESET if _on() else s


def acc(s): return _paint("acc", s)
def dim(s): return _paint("dim", s)
def warn(s): return _paint("warn", s)
def err(s): return _paint("err", s)
def info(msg): print(dim(msg), flush=True)
def error(msg): print(err(" ".join(str(msg).split())), flush=True)
def prompt_str(model_label): return dim(model_label + " ") + acc("› ")


def _trunc(s, width):
    return s if len(s) <= width else s[:max(width - 1, 1)] + "…"


def tool_line(name, arg):
    w = shutil.get_terminal_size((80, 24)).columns
    print(dim(_trunc("  %s  %s" % (name, " ".join(str(arg).split())), w - 1)), flush=True)


def tool_done(summary): print(dim("  " + summary), flush=True)


class Stream:
    """Token stream with light markdown. Char state machine, so markers split across tokens work."""

    def __init__(self):
        self.started = self.nl = False   # nl: next char starts an indented line
        self.bol = True                  # at beginning of a source line (fence detection)
        self.ticks = 0                   # pending leading backticks
        self.star = self.skip = self.fence = self.bold = self.code = False
        self.out = []

    def _emit(self, c):
        if self.nl:
            self.out.append("    " if self.fence else "  ")
            self.nl = False
        self.out.append(c)

    def _tick(self):  # inline backtick toggle
        self.code = not self.code
        self.out.append(_fg("acc") if self.code else _rs() + (BOLD if self.bold and _on() else ""))

    def _star2(self):
        self.bold = not self.bold
        self.out.append(BOLD if self.bold and _on() else _rs())

    def _ch(self, c):
        if self.star:
            self.star = False
            if c == "*":
                return self._star2()
            self._emit("*")
        if self.bol and c == "`" and not self.code:
            self.ticks += 1
            if self.ticks == 3:
                self.ticks, self.fence, self.skip, self.bol = 0, not self.fence, True, False
                self.out.append(_fg("dim") if self.fence else _rs())
            return
        if self.ticks:  # 1-2 leading backticks were inline code after all
            n, self.ticks = self.ticks, 0
            for _ in range(n):
                self._tick()
        self.bol = False
        if self.skip:
            if c == "\n":
                self.skip, self.bol = False, True
            return
        if c == "\n":
            if not self.fence and (self.bold or self.code):
                self.bold = self.code = False
                self.out.append(_rs())
            self.out.append("\n")
            self.nl = self.bol = True
        elif self.fence:
            self._emit(c)
        elif c == "*":
            self.star = True
        elif c == "`":
            self._tick()
        else:
            self._emit(c)

    def write(self, token):
        if not token:
            return
        if not self.started:
            self.started = True
            self.out.append(acc("▍") + " ")
        for c in token:
            self._ch(c)
        sys.stdout.write("".join(self.out))
        sys.stdout.flush()
        self.out = []

    def end(self):
        if not self.started:
            return
        if self.star:
            self._emit("*")
        for _ in range(self.ticks):
            self._emit("`")
        self.out.append(_rs() + ("\n" if self.nl else "\n\n"))
        sys.stdout.write("".join(self.out))
        sys.stdout.flush()
        self.__init__()


@contextlib.contextmanager
def spinner(label=""):
    if not sys.stdout.isatty():
        yield
        return
    stop = threading.Event()

    def run():
        i = 0
        while not stop.wait(0.08):
            sys.stdout.write("\r" + dim("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[i % 10] + (" " + label if label else "")))
            sys.stdout.flush()
            i += 1
    t = threading.Thread(target=run, daemon=True)
    sys.stdout.write("\x1b[?25l")
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join()
        sys.stdout.write("\r\x1b[2K\x1b[?25h")
        sys.stdout.flush()


def menu(title, items, hint=True):
    """Return chosen index, or None on esc / invalid input."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(title)
        for i, it in enumerate(items, 1):
            print("  %d. %s" % (i, it))
        try:
            n = int(input("> ").strip()) - 1
        except (ValueError, EOFError):
            return None
        return n if 0 <= n < len(items) else None
    import termios, tty
    fd, old, cur = sys.stdin.fileno(), None, 0
    n_lines = len(items) + 1 + bool(hint)
    old = termios.tcgetattr(fd)

    def draw(first):
        w = shutil.get_terminal_size((80, 24)).columns - 1
        s = "" if first else "\x1b[%dA" % n_lines
        rows = [title] + [("› " if i == cur else "  ") + _trunc(it, w - 2) for i, it in enumerate(items)]
        s += "".join("\r\x1b[2K" + (acc(r) if i == cur + 1 else dim(r) if i else r) + "\n" for i, r in enumerate(rows))
        if hint:
            s += "\r\x1b[2K" + dim("↑↓ · enter · esc") + "\n"
        sys.stdout.write(s)
        sys.stdout.flush()
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[?25l")
        draw(True)
        while True:
            c = os.read(fd, 1)
            if c == b"\x1b":
                if not select.select([fd], [], [], 0.05)[0]:
                    return None
                seq = os.read(fd, 2)
                c = {b"[A": b"k", b"[B": b"j"}.get(seq, b"")
            if c in (b"\r", b"\n"):
                return cur
            if c == b"\x03":
                raise KeyboardInterrupt
            if c == b"k":
                cur = (cur - 1) % len(items)
            elif c == b"j":
                cur = (cur + 1) % len(items)
            else:
                continue
            draw(False)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[%dA\x1b[J\x1b[?25h" % n_lines)
        sys.stdout.flush()


_hist_loaded = False


def ask(prompt):
    """input() with persistent history; EOFError propagates."""
    global _hist_loaded
    hf = os.path.join(os.environ.get("MICRO_HOME") or os.path.expanduser("~/.config/microagent"), "history")
    try:
        import readline
    except ImportError:
        readline = None
    if readline and not _hist_loaded:
        _hist_loaded = True
        readline.set_history_length(1000)
        with contextlib.suppress(OSError):
            readline.read_history_file(hf)
    line = input(re.sub(r"(\x1b\[[0-9;]*m)", "\001\\1\002", prompt))
    if readline and line.strip():
        with contextlib.suppress(OSError):
            os.makedirs(os.path.dirname(hf), exist_ok=True)
            readline.write_history_file(hf)
    return line


def confirm(kind, text):
    print(warn(kind) + "  " + text)
    print(dim("[enter] ok · [n] no"), flush=True)
    try:
        return input("").strip().lower() in ("", "y", "yes")
    except EOFError:
        return False


def selftest():
    global _force
    _plain = _run_stream
    os.environ["NO_COLOR"] = "1"
    assert acc("x") == dim("x") == warn("x") == err("x") == "x"  # NO_COLOR -> plain
    assert _plain(["**bo", "ld** and `co", "de`\n``", "`\nx *y*\n``", "`\nz"]) == "▍ bold and code\n    x *y*\n  z\n\n"
    assert _plain(list("a*b ``c`")) == "▍ a*b c\n\n"  # lone * stays, inline double tick
    _force = True  # colour on: bold + code markers become escapes, split tokens give the same bytes
    one, two = _run_stream(["**b** `c`"]), _run_stream(["*", "*b*", "* `", "c", "`"])
    assert one == two and BOLD in one and "\x1b[38;" in one and "**" not in one
    _force = None
    os.environ.pop("NO_COLOR")
    _sin, sys.stdin = sys.stdin, io.StringIO("2\n")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            assert menu("t", ["a", "b", "c"]) == 1
            sys.stdin = io.StringIO("9\n")
            assert menu("t", ["a"]) is None
    finally:
        sys.stdin = _sin
    assert _trunc("abcdef", 4) == "abc…" and _trunc("abc", 4) == "abc"
    assert prompt_str("local · m") == "local · m › "
    print("selftest ok")


def _run_stream(tokens):
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        s = Stream()
        for t in tokens:
            s.write(t)
        s.end()
    finally:
        sys.stdout = old
    return buf.getvalue()


if __name__ == "__main__":
    selftest()
