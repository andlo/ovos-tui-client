#!/usr/bin/env python3
from setuptools import setup, find_packages
from os import path

BASE_PATH = path.abspath(path.dirname(__file__))


def get_version():
    version_file = path.join(BASE_PATH, "version.py")
    major, minor, build, alpha = (None, None, None, None)
    with open(version_file) as f:
        for line in f:
            if "VERSION_MAJOR" in line:
                major = line.split("=")[1].strip()
            elif "VERSION_MINOR" in line:
                minor = line.split("=")[1].strip()
            elif "VERSION_BUILD" in line:
                build = line.split("=")[1].strip()
            elif "VERSION_ALPHA" in line:
                alpha = line.split("=")[1].strip()
            if (major and minor and build and alpha) or "# END_VERSION_BLOCK" in line:
                break
    version = f"{major}.{minor}.{build}"
    if alpha and int(alpha) > 0:
        version += f"a{alpha}"
    return version


def get_requirements(requirements_filename):
    with open(path.join(BASE_PATH, requirements_filename), "r", encoding="utf-8") as r:
        lines = r.readlines()
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


with open("README.md", "r") as f:
    long_description = f.read()

setup(
    name="ovos-tui-client",
    version=get_version(),
    description="A split-pane terminal UI for testing OVOS without a mic/speaker",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/andlo/ovos-tui-client",
    project_urls={
        "Source": "https://github.com/andlo/ovos-tui-client",
        "Bug Tracker": "https://github.com/andlo/ovos-tui-client/issues",
    },
    author="Andreas Lorensen",
    author_email="andlo@outlook.dk",
    license="GPL-3.0-or-later",
    python_requires=">=3.9",
    packages=find_packages(include=["ovos_tui_client", "ovos_tui_client.*"]),
    install_requires=get_requirements("requirements.txt"),
    keywords="ovos textual tui cli-client messagebus voice-assistant testing",
    entry_points={"console_scripts": ["ovos-tui=ovos_tui_client.app:run"]},
)
