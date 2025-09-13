# Configuration file for the Sphinx documentation builder.
#
# For the full list of options, see:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys

# -- Path setup --------------------------------------------------------------

# Add project root to sys.path so autodoc can import modules
sys.path.insert(0, os.path.abspath(".."))

# Configure Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "publik_famille_demo.settings")

import django
django.setup()

# -- Project information -----------------------------------------------------

project = "Publik Famille Demo"
author = "Entr'ouvert Demo Team"
release = "1.0"
version = "1.0.0"

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",      # API documentation from docstrings
    "sphinx.ext.viewcode",     # Add links to highlighted source code
    "sphinx.ext.napoleon",     # Support for NumPy/Google style docstrings
]

templates_path = ["_templates"]
exclude_patterns = []

language = "fr"

# -- Options for HTML output -------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

# -- Autodoc settings --------------------------------------------------------

autodoc_member_order = "bysource"   # Keep same order as in source files
autodoc_typehints = "description"   # Show type hints in the description
