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
- **Tippecanoe**, a vector-tile builder that provides `tippecanoe`,
  `tile-join`, and `tippecanoe-decode` for map output.
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

## Install Tippecanoe map tools

The quickstart needs Tippecanoe to generate and validate map tiles. The
upstream project documents building from source on Ubuntu. Install its compiler
and library prerequisites, then build and install it:

```sh
sudo apt update
sudo apt install -y build-essential libsqlite3-dev zlib1g-dev
git clone https://github.com/felt/tippecanoe.git /tmp/tippecanoe
make -C /tmp/tippecanoe -j"$(nproc)"
sudo make -C /tmp/tippecanoe install
```

The upstream instructions require a C++17-capable compiler. If compilation
fails, follow the compiler guidance in the
[Tippecanoe README](https://github.com/felt/tippecanoe#development). Verify
that the commands are available:

```sh
tippecanoe --version
command -v tile-join
command -v tippecanoe-decode
```

## GDAL raster dependencies

Local raster acquisition also requires GDAL:

```sh
sudo apt install -y gdal-bin
```

After creating the Python environment, run the
[offline LERC check](troubleshooting.md#lerc-raster-dependency-check).
Ubuntu/WSL codec support is not yet validated for this project; a successful
package installation alone does not prove the required reader works.

## Option: For cloud instances

Cloud commands need the Google Cloud CLI (`gcloud`). When you run the cloud
quickstart, it can offer to configure Google's apt repository and install the
`google-cloud-cli` package. The installation requires confirmation and may ask
for your Ubuntu administrator password. You can also install it manually using
Google's [Debian/Ubuntu instructions](https://docs.cloud.google.com/sdk/docs/install-sdk#deb).
Then authenticate:

```sh
gcloud --version
gcloud auth login
```

The quickstart does not change the global `gcloud` project setting; the selected
cloud profile supplies the project.

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