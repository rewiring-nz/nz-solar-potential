# macOS environment setup

These instructions set up an existing checkout of
`nz-solar-potential`; they do not download source data or configure a LINZ key.

## What you will install

- **Terminal**, which runs the commands in this guide. It is already included
  with macOS.
- **Homebrew**, a package manager that installs and updates command-line
   applications on a Mac. It installs the project tools listed in its
   `Brewfile`.
- **Git**, which downloads project changes and records changes you make.
- **Python**, the programming language and runtime that runs the solar
   data-processing scripts.

This project has its own `Brewfile` used by Homebrew. It installs applications used to build datasets.

## Install Homebrew

Check whether Homebrew is already available. Open **Terminal** from
Applications > Utilities, or search for it with Spotlight, then run:

```sh
brew --version
```

If it prints a version number, Homebrew is already installed.
If it says `command not found`, install Homebrew using its official installer:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the prompts. At the end, Homebrew may print a command beginning with
`eval` to add Homebrew to your shell path. Copy and run that command exactly as
shown. Close Terminal, open it again, then confirm Homebrew is available:

```sh
brew --version
```

If this command says `command not found`, repeat the path command printed by
the installer or use the [Homebrew installation guide](https://docs.brew.sh/Installation).

## Open the project directory

In Terminal, change into your local project checkout. For example, if it is in
the default workspace used by this project:

```sh
cd ~/nz-solar-potential
```

## Install project tools

The project's `Brewfile` is a list of required Mac applications. Homebrew
compares it with the tools already on the computer and installs anything that
is missing. It does not install the project's Python packages yet.

Run:

```sh
brew bundle --file=Brewfile
```

This installs any Brewfile tools that are missing; it does not update every
installed Homebrew package. Homebrew may finish by reporting that many
formulae or casks are outdated. That is an informational count, not a failure
and not a request to upgrade them for this project. Do not run `brew upgrade`
just because this count appears. The command is successful if it exits with
status 0 and returns to the shell prompt; investigate actual `Error:` messages
or a nonzero exit status.

This installs Git, which retrieves and records project changes, Python,
which runs the data-processing scripts, and Tippecanoe, which provides the
map-tile commands `tippecanoe`, `tile-join`, and `tippecanoe-decode`. Confirm
the required commands are available:

```sh
git --version
python3 --version
tippecanoe --version
command -v tile-join
command -v tippecanoe-decode
```

## Next step

Return to [Create the project Python environment](local-setup.md#create-the-project-python-environment)
to install the project's required Python packages and complete local setup.

## Troubleshooting

| Problem | First action |
| --- | --- |
| `brew: command not found` | Reopen Terminal and run the path command printed by the Homebrew installer. |
| `brew bundle` says many formulae or casks are outdated | Informational only. If the command exits successfully, continue; do not upgrade unrelated packages just to clear the count. |
| `brew bundle` exits nonzero or prints `Error:` | Confirm that Terminal is in the project directory and `Brewfile` is present (`ls Brewfile`). Follow the specific error; the outdated-package count alone is not the cause. |
| `tippecanoe`, `tile-join`, or `tippecanoe-decode: command not found` | Run `brew bundle --file=Brewfile` from the project directory, then open a new Terminal and verify each command with `command -v`. |
| `python3: command not found` | Run `brew bundle --file=Brewfile`, then close and reopen Terminal. |
