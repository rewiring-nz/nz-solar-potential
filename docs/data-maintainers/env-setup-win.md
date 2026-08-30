# Windows environment setup

> **Warning: Untested for this project:** This guide is a proposed setup path and has
> not yet been verified by a `nz-solar-potential` data maintainer. Report any
> differences or failures when testing it.

Use Windows Subsystem for Linux (WSL2) with Ubuntu rather than a native Windows
Python environment. The project's build scripts use a POSIX shell and the
geospatial Python stack is generally more reliable in Linux.

## What you will install

- **PowerShell**, a command window included with Windows, used to install and
  manage WSL.
- **WSL2 and Ubuntu**, a Linux environment running on Windows. It provides the
  command style required by the project's build scripts.
- **Git**, installed inside Ubuntu to retrieve the project and record changes.
- **Python**, installed inside Ubuntu to run the data-processing scripts.

## Install WSL2 and Ubuntu

First check whether WSL is already available. Open **Windows PowerShell** as
an Administrator and run:

```powershell
wsl --status
```

If it reports a version 2 installation with Ubuntu available, open **Ubuntu**
from the Start menu and continue to [Install Git](#install-git). Otherwise,
run:

```powershell
wsl --install -d Ubuntu
```

Restart Windows when prompted. Open **Ubuntu** from the Start menu. On first
launch, choose a Linux user name and password. This password is for Ubuntu
administration; keep it safe.

Use the [Microsoft WSL installation guide](https://learn.microsoft.com/windows/wsl/install)
if the install command reports an error.

## Install Git

Git downloads the project and records changes. In the Ubuntu terminal, check
whether it is already available:

```sh
git --version
```

If it prints a version number, keep the installed Git. If it says `command not
found`, install it. Enter the Ubuntu password when `sudo` asks for it; nothing
appears while the password is typed.

```sh
sudo apt update
sudo apt install -y git
```

Confirm the installation:

```sh
git --version
```

## Install Python

Python runs the project's data-processing scripts. The `venv` package creates
an isolated environment for this project and `pip` installs its required
packages. Check whether Python is available:

```sh
python3 --version
```

If it prints a version number, continue. If it says `command not found`,
install Python and its project-environment tools:

```sh
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

Confirm the installation:

```sh
python3 --version
```

## Keep the project inside Linux

Store the project and its large `data/` directory under your Ubuntu home
directory, such as `~/nz-solar-potential`, rather than under
`/mnt/c/`. Accessing many files through the mounted Windows filesystem can make
geospatial processing much slower.

Clone or otherwise obtain the project using the repository location supplied by
the project maintainer, then change to the folder containing `requirements.txt`
and `.env.example`:

```sh
cd ~/nz-solar-potential
ls requirements.txt .env.example
```

Return to [Create the project Python environment](local-setup.md#create-the-project-python-environment)
to install the project's required Python packages and complete local setup.