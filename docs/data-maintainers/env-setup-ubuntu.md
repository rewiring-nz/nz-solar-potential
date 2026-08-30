# Ubuntu environment setup

> **Warning: Untested for this project:** This guide is a proposed setup path and has
> not yet been verified by a `nz-solar-potential` data maintainer. Report any
> differences or failures when testing it.

This guide uses Ubuntu's standard package manager, `apt`, to install the tools
needed to run the project locally.

## What you will install

- **Terminal**, which is already included with Ubuntu.
- **Git**, which downloads project changes and records changes you make.
- **Python**, the programming language and runtime that runs the solar
  data-processing scripts.
- **apt**, Ubuntu's included package manager, which installs and updates Git
  and Python in the steps below.

## Install Git

Git is needed to retrieve the project and record changes. Open Terminal and
check whether it is already available:

```sh
git --version
```

If it prints a version number, keep the installed Git. If it says `command not
found`, install it:

```sh
sudo apt update
sudo apt install -y git
```

Enter your Ubuntu password when asked. Nothing appears while the password is
typed; this is normal. Confirm the installation:

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

## Open the project directory

Obtain the project using the repository location supplied by the project
maintainer. Then use `cd` to enter its directory. For example:

```sh
cd ~/nz-solar-potential
ls requirements.txt .env.example
```

The command should list both files. If it does not, move to the folder that
contains them before continuing.

Return to [Create the project Python environment](local-setup.md#create-the-project-python-environment)
to install the project's required Python packages and complete local setup.