#!/usr/bin/env python3
"""
Setup configuration for Memoria package.

This makes the common modules importable across all processors and
provides a command-line entry point for the memoria script.

Install in development mode: pip install -e .
"""

from pathlib import Path
from setuptools import setup, find_packages

# Read the README for long description
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

# Read requirements from requirements.txt
requirements_file = Path(__file__).parent / "requirements.txt"
requirements = []
if requirements_file.exists():
    with open(requirements_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                requirements.append(line)

setup(
    name="memoria",
    version="0.1.0",
    description="A comprehensive suite of tools for processing and organizing media exports from social media platforms",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="your_username",
    author_email="ethan@ethanriver.page",  
    url="https://github.com/your_username/memoria",  
    packages=find_packages(include=["common", "common.*", "processors", "processors.*"]),
    python_requires=">=3.7",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "memoria=memoria:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "Intended Audience :: Developers",
        "Topic :: Multimedia :: Graphics",
        "Topic :: Multimedia :: Video",
        "Topic :: System :: Archiving",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
        "Environment :: Console",
    ],
    keywords="social-media media-processing metadata exif photos videos instagram snapchat google-photos archiving",
    project_urls={
        "Bug Reports": "https://github.com/your_username/memoria/issues",
        "Source": "https://github.com/your_username/memoria",
        "Documentation": "https://github.com/your_username/memoria#readme",
    },
)
