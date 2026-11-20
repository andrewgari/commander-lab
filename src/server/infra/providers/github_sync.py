import os
import json
import httpx
from datetime import datetime

CACHE_VERSION_KEY = "github_latest_version"
STALE_VERSION_KEY = "github_latest_version_stale"
CACHE_CHANGELOG_KEY = "github_changelog"
STALE_CHANGELOG_KEY = "github_changelog_stale"
CACHE_TTL = 600

async def update_github_data(r):
    try:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "commander-lab",
        }
        if "GITHUB_TOKEN" in os.environ:
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            # 1. Fetch version from tags
            tags_resp = await client.get("https://api.github.com/repos/andrewgari/commander-lab/tags", headers=headers)
            tags_resp.raise_for_status()
            tags_data = tags_resp.json()
            
            version = None
            if tags_data:
                version = tags_data[0]["name"]
                if version.startswith("v"):
                    version = version[1:]
            
            if version:
                r.setex(CACHE_VERSION_KEY, CACHE_TTL, version)
                r.set(STALE_VERSION_KEY, version)
                
            # 2. Fetch changelog
            releases = []
            releases_resp = await client.get("https://api.github.com/repos/andrewgari/commander-lab/releases", headers=headers)
            releases_resp.raise_for_status()
            releases_data = releases_resp.json()
            
            if releases_data:
                for rel in releases_data:
                    releases.append({
                        "tag_name": rel.get("tag_name"),
                        "published_at": rel.get("published_at"),
                        "body": rel.get("body", "")
                    })
            else:
                # Fallback to commits for the recent tags
                if tags_data:
                    for tag in tags_data[:10]:
                        commit_sha = tag["commit"]["sha"]
                        commit_resp = await client.get(f"https://api.github.com/repos/andrewgari/commander-lab/commits/{commit_sha}", headers=headers)
                        if commit_resp.status_code == 200:
                            commit_data = commit_resp.json()
                            releases.append({
                                "tag_name": tag["name"],
                                "published_at": commit_data["commit"]["committer"]["date"],
                                "body": commit_data["commit"]["message"]
                            })
                else:
                    # Fallback to recent commits
                    commits_resp = await client.get("https://api.github.com/repos/andrewgari/commander-lab/commits?per_page=15", headers=headers)
                    if commits_resp.status_code == 200:
                        commits_data = commits_resp.json()
                        for idx, c in enumerate(commits_data):
                            releases.append({
                                "tag_name": f"commit-{c['sha'][:7]}",
                                "published_at": c["commit"]["committer"]["date"],
                                "body": c["commit"]["message"]
                            })
                            
            if releases:
                serialized = json.dumps(releases)
                r.setex(CACHE_CHANGELOG_KEY, CACHE_TTL, serialized)
                r.set(STALE_CHANGELOG_KEY, serialized)
                
    except Exception as e:
        print(f"Error updating github data: {e}")
