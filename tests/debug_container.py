#!/usr/bin/env python3
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Debug script to inspect the test container while it's running.
This helps us understand why the ingest service isn't processing files.
"""

import pytest
import time
import subprocess


def test_debug_container_services(cwa_container, test_volumes, ingest_folder):
    """
    Debug test to inspect container state.
    This test will keep the container running and show diagnostic info.
    """
    print("\n" + "="*80)
    print("🔍 DEBUGGING TEST CONTAINER")
    print("="*80)

    # cwa_container is the DockerCompose instance
    compose = cwa_container

    # Show container status
    print("\n📦 Container Status:")
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=temp-cwa-test-suite"],
        capture_output=True, text=True
    )
    print(result.stdout)

    # Check if container is actually running
    print("\n🏃 Container Logs (last 50 lines):")
    result = subprocess.run(
        ["docker", "logs", "--tail", "50", "temp-cwa-test-suite"],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    # Check s6-overlay services
    print("\n🔧 S6 Services Status:")
    result = subprocess.run(
        ["docker", "exec", "temp-cwa-test-suite", "s6-rc", "-a", "list"],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"❌ Failed to list s6 services (exit code {result.returncode})")
        print("STDERR:", result.stderr)

    # Check if specific services are up
    print("\n🎯 Critical Services Check:")
    critical_services = [
        "cwa-init",
        "svc-calibre-web-automated",
        "cwa-ingest-service",
    ]

    for service in critical_services:
        result = subprocess.run(
            ["docker", "exec", "temp-cwa-test-suite", "s6-svstat", f"/run/service/{service}"],
            capture_output=True, text=True
        )
        status = "✅ UP" if result.returncode == 0 else "❌ DOWN"
        print(f"{status} {service}")
        if result.stdout:
            print(f"     {result.stdout.strip()}")

    # Check volume mounts
    print("\n💾 Volume Mounts:")
    result = subprocess.run(
        ["docker", "inspect", "temp-cwa-test-suite", "--format", "{{json .Mounts}}"],
        capture_output=True, text=True
    )
    print(result.stdout)

    # Check directory contents
    print("\n📁 Directory Structure:")
    dirs_to_check = [
        "/config",
        "/cwa-book-ingest",
        "/calibre-library",
    ]

    for dir_path in dirs_to_check:
        print(f"\n{dir_path}:")
        result = subprocess.run(
            ["docker", "exec", "temp-cwa-test-suite", "ls", "-la", dir_path],
            capture_output=True, text=True
        )
        print(result.stdout)

    # Check if inotify is working
    print("\n👁️ File Watcher Check:")
    result = subprocess.run(
        ["docker", "exec", "temp-cwa-test-suite", "ps", "aux"],
        capture_output=True, text=True
    )
    print("Processes related to ingest/watch:")
    for line in result.stdout.split('\n'):
        if any(keyword in line.lower() for keyword in ['ingest', 'watch', 'inotify']):
            print(f"  {line}")

    # Check environment variables
    print("\n🌍 Environment Variables:")
    result = subprocess.run(
        ["docker", "exec", "temp-cwa-test-suite", "env"],
        capture_output=True, text=True
    )
    for line in sorted(result.stdout.split('\n')):
        if any(var in line for var in ['CWA_', 'PUID', 'PGID', 'TZ', 'NETWORK']):
            print(f"  {line}")

    # Now drop a test file and watch what happens
    print("\n" + "="*80)
    print("🧪 DROPPING TEST FILE")
    print("="*80)

    test_file = ingest_folder / "debug_test.txt"
    test_file.write_text("This is a test file for debugging")
    print(f"✓ Created: {test_file}")
    print(f"✓ File size: {test_file.stat().st_size} bytes")

    # Watch the ingest folder from inside the container
    print("\n⏳ Watching /cwa-book-ingest for 30 seconds...")
    for i in range(6):
        time.sleep(5)
        result = subprocess.run(
            ["docker", "exec", "temp-cwa-test-suite", "ls", "-la", "/cwa-book-ingest"],
            capture_output=True, text=True
        )
        print(f"\n[{(i+1)*5}s] /cwa-book-ingest contents:")
        print(result.stdout)

        if "debug_test.txt" not in result.stdout:
            print("❌ File was consumed/deleted!")
            break
    else:
        print("⚠️ File still exists after 30 seconds - not being processed")

    # Check logs again to see if anything changed
    print("\n📋 Recent Container Logs (last 30 lines):")
    result = subprocess.run(
        ["docker", "logs", "--tail", "30", "temp-cwa-test-suite"],
        capture_output=True, text=True
    )
    print(result.stdout)

    print("\n" + "="*80)
    print("🔍 DEBUG COMPLETE")
    print("="*80)

    # Don't fail the test - we're just debugging
    assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
