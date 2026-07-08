#!/usr/bin/env python3
import os
import subprocess
import sys

def run_command(cmd, check=True):
    try:
        result = subprocess.run(cmd, check=check, text=True, capture_output=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running {' '.join(cmd)}:")
        print(e.stderr)
        if check:
            sys.exit(1)
        return None

def get_version_from_toml():
    if not os.path.exists("pyproject.toml"):
        return None
    with open("pyproject.toml", "r", encoding="utf-8") as f:
        in_project = False
        for line in f:
            line = line.strip()
            # Ignore comments and empty lines
            if not line or line.startswith("#"):
                continue
            if line.startswith("[project]"):
                in_project = True
            elif line.startswith("[") and line != "[project]":
                in_project = False
            
            if in_project and line.startswith("version"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip('"').strip("'")
    return None

def main():
    # Ensure running from repo root
    if not os.path.exists(".git"):
        print("Error: Must run from the root of the git repository.")
        sys.exit(1)

    version = get_version_from_toml()
    if not version:
        print("Error: Could not find version under [project] in pyproject.toml.")
        sys.exit(1)

    tag_name = f"v{version}"
    print(f"Detected version in pyproject.toml: {version}")
    print(f"Target git tag: {tag_name}")

    # 1. Check working directory status
    status = run_command(["git", "status", "--porcelain"])
    if status:
        print("\nWarning: You have uncommitted changes:")
        print(status)
        confirm = input("\nDo you want to proceed anyway? (y/N): ").strip().lower()
        if confirm != 'y':
            print("Aborted.")
            sys.exit(0)

    # 2. Check current branch
    branch = run_command(["git", "branch", "--show-current"])
    print(f"Current branch: {branch}")
    if branch not in ["main", "master"]:
        confirm = input(f"Warning: You are on branch '{branch}', not main/master. Proceed? (y/N): ").strip().lower()
        if confirm != 'y':
            print("Aborted.")
            sys.exit(0)

    # 3. Check if local tag already exists
    local_tags = run_command(["git", "tag"]).splitlines()
    if tag_name in local_tags:
        print(f"\nWarning: Local tag '{tag_name}' already exists.")
        action = input("Do you want to re-create it on the current HEAD? (y/N): ").strip().lower()
        if action == 'y':
            print(f"Deleting local tag '{tag_name}'...")
            run_command(["git", "tag", "-d", tag_name])
        else:
            print("Aborted.")
            sys.exit(0)

    # 4. Confirm release
    confirm = input(f"\nCreate and push tag '{tag_name}'? (y/N): ").strip().lower()
    if confirm != 'y':
        print("Aborted.")
        sys.exit(0)

    # 5. Create local tag
    print(f"Creating local tag '{tag_name}'...")
    run_command(["git", "tag", tag_name])

    # 6. Push tag
    print(f"Pushing tag '{tag_name}' to origin...")
    try:
        subprocess.run(["git", "push", "origin", tag_name], check=True)
        print(f"\nSuccess! Tag '{tag_name}' pushed successfully.")
        print("GitHub Actions will now build and publish the release package to PyPI.")
    except subprocess.CalledProcessError:
        print(f"\nError pushing tag. The tag '{tag_name}' might already exist on remote.")
        action = input("Do you want to force-overwrite the remote tag? (y/N): ").strip().lower()
        if action == 'y':
            print(f"Deleting remote tag '{tag_name}'...")
            run_command(["git", "push", "origin", "--delete", tag_name], check=False)
            print("Re-pushing tag...")
            run_command(["git", "push", "origin", tag_name])
            print(f"\nSuccess! Tag '{tag_name}' force-pushed successfully.")
        else:
            print("Aborted.")
            sys.exit(1)

if __name__ == "__main__":
    main()
