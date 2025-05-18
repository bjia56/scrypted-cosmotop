#!/usr/bin/env python3

import sys
from packaging.version import Version, InvalidVersion

def main():
    if len(sys.argv) != 2:
        print("Usage: find_latest_version.py <tags_file>", file=sys.stderr)
        sys.exit(1)

    tags_file = sys.argv[1]
    with open(tags_file) as f:
        tags = [line.strip().lstrip("v") for line in f if line.strip()]

    versions = []
    for tag in tags:
        try:
            versions.append(Version(tag))
        except InvalidVersion:
            pass

    if not versions:
        print("No valid version tags found!", file=sys.stderr)
        sys.exit(1)

    latest = max(versions)
    print(f"LATEST=v{latest}")

    # Is it a prerelease?
    if latest.is_prerelease:
        print("IS_BETA=true")
    else:
        print("IS_BETA=false")

if __name__ == "__main__":
    main()