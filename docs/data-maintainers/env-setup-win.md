# Windows setup

> Untested for this project. Report anything that differs.

Use WSL2 with Ubuntu, not native Windows Python. The build scripts need a
POSIX shell.

## 1. WSL2 and Ubuntu

In PowerShell (as Administrator):

```powershell
wsl --status
```

If WSL 2 with Ubuntu is not there:

```powershell
wsl --install -d Ubuntu
```

Restart when asked, open **Ubuntu** from the Start menu, and choose a Linux
user name and password. Help: [Microsoft WSL guide](https://learn.microsoft.com/windows/wsl/install).

## 2. Tools inside Ubuntu

Run everything from here on in the Ubuntu terminal, not PowerShell. Follow the
[Ubuntu setup](env-setup-ubuntu.md) for Git, Python, GDAL, Tippecanoe and
(for cloud work) `gcloud`. The quickstart never installs `gcloud` into Windows.

## 3. Keep the project inside Linux

Put the project and its `data/` folder under your Ubuntu home, e.g.
`~/nz-solar-potential`, not under `/mnt/c/`. The Windows file system is much
slower for this work.

```sh
cd ~/nz-solar-potential
ls requirements.txt .env.example
```

Then [create the Python environment](local-setup.md#create-the-project-python-environment).
