# Installation

Install **cursaves** from the Royal Import fork. This build includes Windows support (macOS and Linux are also supported).

**Requirements:** Python 3.10+, [uv](https://docs.astral.sh/uv/), Git (for the git sync backend).

## First-time install (each machine)

Run in PowerShell, Terminal, or bash — **not** in Cursor's integrated terminal.

```bash
uv tool install git+https://github.com/royalimport/cursaves.git
```

With S3 backend support:

```bash
uv tool install "cursaves[s3] @ git+https://github.com/royalimport/cursaves.git"
```

### PATH

If `cursaves` is not found after install:

```bash
uv tool update-shell
```

On Windows, `cursaves` is installed to `%USERPROFILE%\.local\bin`. Add that directory to PATH if `uv tool update-shell` does not pick it up.

Restart the terminal after updating PATH.

## Reinstall / upgrade

Use this when the fork has new commits, or to replace an install from upstream:

```bash
uv tool install --reinstall git+https://github.com/royalimport/cursaves.git
```

Or upgrade without changing the source URL (if already installed from this fork):

```bash
uv tool upgrade cursaves
```

## One-time sync setup (each machine)

Create a **private** git repo for chat snapshots, then:

```bash
cursaves init --remote git@github.com:you/my-cursaves-data.git
```

Use the same remote on every machine you sync between.

## Daily use

Quit Cursor before push/pull (it locks the SQLite database).

```bash
cd /path/to/your/project
cursaves push              # save and upload chats
cursaves pull              # download and restore chats
```

Restart Cursor after `pull` to see imported conversations.

### Sync one workspace only

List workspaces:

```bash
cursaves workspaces
```

Push or pull a single workspace by number, hash, or path substring:

```bash
cursaves push -w 9
cursaves push -w c42e6cce
cursaves pull -w royalimportodoo
```

Avoid `-g` unless you intend to sync **all** workspaces on the machine.

## Hands-off background sync (Windows)

After `init`, on each machine:

```bash
cursaves autosync install
```

This runs a hidden sync at logon and every 15 minutes, and adds a **Cursor (synced)** Start Menu shortcut. Imports only happen while Cursor is closed. See the README's `autosync` section for details; check it with `cursaves autosync status`.

After upgrading cursaves, re-run `cursaves autosync install` so the task points at the current install.

## Install from a local clone (development)

```bash
git clone https://github.com/royalimport/cursaves.git
cd cursaves
uv tool install --reinstall .
```

## Verify

```bash
cursaves --version
cursaves workspaces
```

On Windows, `workspaces` should list local paths like `c:\Users\...\your-project` without an "Unsupported platform" error.
