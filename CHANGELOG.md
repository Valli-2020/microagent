# Changelog

## 1.4.1
- Installer (zsh): the PATH line goes into `~/.zshenv` instead of the end of `~/.zshrc`, so it no longer lands after plugins that must be sourced last.
- The PATH line uses `$HOME/...` instead of the expanded home path, so a `.zshrc`/`.zshenv` kept in a dotfiles repo stays machine-independent.
- `--uninstall` also removes the line from `.zshenv` and from `.zshrc` (older installs).

## 1.4.0
- New base personality: check facts with tools instead of assuming (fixed procedure and examples, so small local models follow it).
- `effort` setting (`low`, `medium`, `high`) in `/settings` and `config.json`: how hard the model checks facts before answering.
- Existing installs keep their `~/.config/microagent/personality.md`; copy the new one over it to get the new prompt.

## 1.3.0
- No code changes. `micro -u` with the `[y/n]` question confirmed on a real install (y updates, n declines).
- Adds this changelog.

## 1.2.1 (update test)
- Test release for the `micro -u` preview and `[y/n]` question.

## 1.2.0
- `/settings` popup menu.
- `micro -u` shows the changes and asks before applying; `micro -u -y` skips the question.

## 1.1.0
- `micro -u`, `micro -v`, one-line installer, skills are offered, not forced.

## 1.0.0
- First release.
