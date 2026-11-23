# micro

A small AI helper for your terminal. One command, no dependencies, any OpenAI-compatible endpoint.

- **Local first.** Defaults to Ollama on `localhost:11434`. Works with llama.cpp, LM Studio, vLLM or any hosted OpenAI-style API. Add and switch models in the TUI with `/model`.
- **Tiny.** About 950 lines of Python, standard library only. No pip, starts instantly.
- **Built-in tools.** Web search and page scraping through [Firecrawl](https://github.com/mendableai/firecrawl)-compatible servers (such as crw), and a shell tool that asks before every command.
- **Skills.** Drop a folder with a `SKILL.md` into your skills directory, like Claude Code. Skills are only offered, never forced: the model mentions a fitting one in a sentence and loads it after you say yes.
- **Your personality, your facts.** `personality.md` is the system prompt, `facts.md` holds what the helper should know about you. Replies are in English unless you write in, or ask for, another language.
- **Quiet look.** One accent colour, no boxes. Respects `NO_COLOR`.

It is a helper, not a coding agent.

## Install

    curl -fsSL https://raw.githubusercontent.com/Valli-2020/microagent/main/install.sh | sh

Needs python3 >= 3.9 and git. The program goes to `~/.local/share/microagent`, the `micro` command to `~/.local/bin` (added to your shell's PATH if missing), your settings to `~/.config/microagent/` (config, personality, facts, skills). It asks once for endpoint, model and crw URL (defaults are used without a TTY). Existing files are never overwritten, and running it again is safe.

Update with `micro -u`. Remove the command with `micro`'s installer: `~/.local/share/microagent/install.sh --uninstall` (config is kept).

Env: `MICRO_DIR` (program dir), `MICRO_HOME` (config dir), `BIN_DIR`, `MICRO_NAME`. If `micro` clashes with the text editor of the same name, use `MICRO_NAME=mi`.

## Usage

    micro

Chat in the terminal; `/help` lists the commands.

## Config

See `examples/config.example.json` for a complete file with fake values.

`~/.config/microagent/config.json` (mode 600):

- `active`: name of the entry in `models` to use
- `models`: `{name: {url, model, key}}`, url is the OpenAI-compatible base (e.g. `http://localhost:11434/v1`)
- `crw`: `{url, key}` for search and scrape
- `skills`: list of skill directories
- `max_turns`: max tool-call rounds per message
- `ctx`: context size in tokens
- `auto`: run bash without asking

Keys can also come from `MICRO_KEY` and `CRW_KEY`.

## Tools

- `search(query)`: web search via crw
- `scrape(url)`: page as markdown (local and private addresses are blocked)
- `bash(cmd)`: shell command, shown and confirmed first unless `auto` is on
- `skill(name)`: load a skill's instructions
- `remember(fact)`: append a line to `facts.md`

## Skills

`<dir>/<name>/SKILL.md` with frontmatter:

    ---
    name: my-skill
    description: When to use this skill, in one line.
    ---
    Instructions...

Only name and description go into the prompt; the model offers a skill briefly and loads the body only after you agree. Add a skill by copying its folder into a skills directory. See `skills/example/`.

## Personality and facts

`personality.md` is the system prompt, `facts.md` holds one fact per line and is appended to it. Both live in the config dir and are yours to edit.

## Safety

- Web, file and tool output is treated as data, not instructions.
- Bash commands need confirmation; actions after web content are confirmed even with `auto`.
- Never put secrets in `facts.md`. Keys stay in `config.json` (mode 600) or env, and are never printed.

## Development

    python3 ma.py --selftest

Runs the built-in tests, including the web, skills and TUI modules. They use local fake servers only; no network or keys needed.

## License

MIT
