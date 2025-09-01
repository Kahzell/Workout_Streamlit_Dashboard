#!/usr/bin/env python3
"""
GitHub Environment Setup Script for Workout Tracker

This script helps set up the required GitHub environment variables
for the Workout Tracker Streamlit app.
"""

import os
import sys

def main():
    print("🔧 GitHub Environment Setup for Workout Tracker")
    print("=" * 50)
    
    # Get GitHub token
    token = input("Enter your GitHub Personal Access Token: ").strip()
    if not token:
        print("❌ GitHub token is required!")
        sys.exit(1)
    
    # Get repository
    repo = input("Enter your GitHub repository (format: username/reponame): ").strip()
    if not repo or '/' not in repo:
        print("❌ Invalid repository format! Use: username/reponame")
        sys.exit(1)
    
    # Get branch (optional)
    branch = input("Enter branch name (default: main): ").strip() or "main"
    
    # Get file paths (optional)
    strength_path = input("Enter strength data file path (default: data/workouts.csv): ").strip() or "data/workouts.csv"
    cardio_path = input("Enter cardio data file path (default: data/cardio.csv): ").strip() or "data/cardio.csv"
    
    # Set environment variables
    env_vars = {
        'GITHUB_TOKEN': token,
        'GITHUB_REPO': repo,
        'GITHUB_BRANCH': branch,
        'GITHUB_FILEPATH_STRENGTH': strength_path,
        'GITHUB_FILEPATH_CARDIO': cardio_path
    }
    
    print("\n🔧 Setting environment variables...")
    for key, value in env_vars.items():
        os.environ[key] = value
        print(f"✅ {key} = {value}")
    
    print("\n🎉 GitHub environment setup complete!")
    print("\n📝 To make these permanent, add them to your system environment or .env file:")
    print()
    for key, value in env_vars.items():
        if key == 'GITHUB_TOKEN':
            print(f"export {key}=***HIDDEN***")
        else:
            print(f"export {key}={value}")
    
    print("\n⚠️  Note: Environment variables set by this script are only temporary.")
    print("   Add them to your system environment or use a .env file for persistence.")

if __name__ == "__main__":
    main()
