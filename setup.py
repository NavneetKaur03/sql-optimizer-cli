# setup.py
# Makes sql_analyzer an installable package.

from setuptools import setup, find_packages

setup(
    name="sql-optimizer-cli",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
)