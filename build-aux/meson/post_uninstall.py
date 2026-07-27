#!/usr/bin/env python3
"""Post-uninstall script to clean up __pycache__ directories."""

import os
import shutil
import sys

def main():
    prefix = os.environ.get('MESON_INSTALL_PREFIX', os.path.expanduser('~/.local'))
    
    # Our Python modules are installed in:
    # $prefix/share/markdown-vault/python/markdown_vault/
    app_python_dir = os.path.join(
        prefix,
        'share',
        'markdown-vault',
        'python',
        'markdown_vault'
    )
    
    # Clean up all __pycache__ directories recursively
    for root, dirs, files in os.walk(app_python_dir):
        if '__pycache__' in dirs:
            pycache_path = os.path.join(root, '__pycache__')
            print(f'Removing {pycache_path}...')
            shutil.rmtree(pycache_path)
    
    # Remove empty directories up to the app_python_dir
    for dirpath, dirnames, filenames in os.walk(app_python_dir, topdown=False):
        if not dirnames and not filenames:
            print(f'Removing empty directory {dirpath}...')
            os.rmdir(dirpath)
    
    # Remove the markdown_vault directory if empty
    if os.path.exists(app_python_dir) and not os.listdir(app_python_dir):
        print(f'Removing empty directory {app_python_dir}...')
        os.rmdir(app_python_dir)
    
    # Remove the python directory if empty
    python_dir = os.path.dirname(app_python_dir)
    if os.path.exists(python_dir) and not os.listdir(python_dir):
        print(f'Removing empty directory {python_dir}...')
        os.rmdir(python_dir)
    
    # Remove the markdown-vault directory if empty
    markdown_vault_dir = os.path.dirname(python_dir)
    if os.path.exists(markdown_vault_dir) and not os.listdir(markdown_vault_dir):
        print(f'Removing empty directory {markdown_vault_dir}...')
        os.rmdir(markdown_vault_dir)

if __name__ == '__main__':
    main()
