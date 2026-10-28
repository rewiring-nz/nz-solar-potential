# Ubuntu setup

> Untested for this project. Report anything that differs.

## 1. Git, Python, GDAL

```sh
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip gdal-bin
git --version
python3 --version
```

Installing `gdal-bin` does not prove LERC decoding works. After creating the
Python environment, run the
[offline LERC check](troubleshooting.md#lerc-raster-dependency-check).

## 2. Tippecanoe

Build it from source (needs a C++17 compiler):

```sh
sudo apt install -y build-essential libsqlite3-dev zlib1g-dev
git clone https://github.com/felt/tippecanoe.git /tmp/tippecanoe
make -C /tmp/tippecanoe -j"$(nproc)"
sudo make -C /tmp/tippecanoe install
tippecanoe --version
command -v tile-join tippecanoe-decode
```

If it fails to compile, see the [Tippecanoe README](https://github.com/felt/tippecanoe#development).

## 3. Cloud only: gcloud

The cloud quickstart can add Google's apt repository and install
`google-cloud-cli` (it asks first). Or follow
[Google's instructions](https://docs.cloud.google.com/sdk/docs/install-sdk#deb). Then:

```sh
gcloud --version
gcloud auth login
```

The quickstart does not change your global `gcloud` project; the cloud
profile sets it.

## Next

```sh
cd ~/nz-solar-potential
ls requirements.txt .env.example
```

Both files should be listed. Then
[create the Python environment](local-setup.md#create-the-project-python-environment).
