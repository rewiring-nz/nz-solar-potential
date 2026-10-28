# macOS setup

Installs the tools for an existing checkout. It does not fetch data or set a
LINZ key.

## 1. Homebrew

In Terminal:

```sh
brew --version
```

If it says `command not found`, install it:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Run the `eval ...` line the installer prints, reopen Terminal, and check
`brew --version` again. Help: [Homebrew installation](https://docs.brew.sh/Installation).

## 2. Project tools

From the project folder (e.g. `cd ~/nz-solar-potential`):

```sh
brew bundle --file=Brewfile
```

This installs Git, GDAL, Python and Tippecanoe if missing. A note that other
packages are outdated is not an error; don't run `brew upgrade` for it.
Check:

```sh
git --version
python3 --version
command -v gdalbuildvrt gdal_translate tile-join tippecanoe-decode
tippecanoe --version
```

The pipeline tests LERC decoding itself and picks a working TIFF reader. See
[LERC dependency check](troubleshooting.md#lerc-raster-dependency-check).

## 3. Cloud only: gcloud

Only needed for cloud builds. Not in the Brewfile.

```sh
brew install --cask gcloud-cli
gcloud --version
gcloud auth login
```

The cloud quickstart offers to install it (and asks first) if Homebrew is
present. See [Team access](team-access.md).

## Next

[Create the Python environment](local-setup.md#create-the-project-python-environment).

## Problems

| Problem | Fix |
| --- | --- |
| `brew: command not found` | Reopen Terminal and run the installer's `eval` line. |
| `brew bundle` exits non-zero or prints `Error:` | Check you are in the folder with `Brewfile` (`ls Brewfile`) and follow the error. |
| `tippecanoe`, `tile-join` or `tippecanoe-decode` not found | Rerun `brew bundle --file=Brewfile`, open a new Terminal, check with `command -v`. |
| `python3: command not found` | Rerun `brew bundle --file=Brewfile`, then reopen Terminal. |
