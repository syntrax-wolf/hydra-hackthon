"""YouTube Search — YouTube Data API v3 for skill gap recommendations.

Searches for playlists and videos for each skill gap.
Requires YOUTUBE_API_KEY in .env.
"""

import logging
import httpx
from core.config import config

log = logging.getLogger("youtube_search")

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


async def search_youtube(query: str, max_results: int = 3) -> list[dict]:
    """Search YouTube for playlists and videos for a skill."""
    if not config.youtube_api_key:
        log.warning("[YOUTUBE] No API key configured")
        return []

    results = []
    async with httpx.AsyncClient(timeout=30) as client:
        # Playlists first (structured learning)
        try:
            resp = await client.get(YOUTUBE_SEARCH_URL, params={
                "part": "snippet",
                "q": f"{query} full course tutorial",
                "type": "playlist",
                "maxResults": max_results,
                "order": "relevance",
                "key": config.youtube_api_key,
            })
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                results.append({
                    "type": "playlist",
                    "title": item["snippet"]["title"],
                    "channel": item["snippet"]["channelTitle"],
                    "description": item["snippet"]["description"][:200],
                    "thumbnail": item["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
                    "url": f"https://www.youtube.com/playlist?list={item['id']['playlistId']}",
                })
        except Exception as e:
            log.warning("[YOUTUBE] Playlist search failed for '%s': %s", query, e)

        # Then individual videos
        try:
            resp = await client.get(YOUTUBE_SEARCH_URL, params={
                "part": "snippet",
                "q": f"{query} tutorial for beginners",
                "type": "video",
                "maxResults": max_results,
                "order": "relevance",
                "videoDuration": "long",
                "key": config.youtube_api_key,
            })
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                results.append({
                    "type": "video",
                    "title": item["snippet"]["title"],
                    "channel": item["snippet"]["channelTitle"],
                    "description": item["snippet"]["description"][:200],
                    "thumbnail": item["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
                    "url": f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                })
        except Exception as e:
            log.warning("[YOUTUBE] Video search failed for '%s': %s", query, e)

    return results


async def get_recommendations_for_gaps(skill_gaps: list[dict]) -> list[dict]:
    """Get YouTube recommendations for the top skill gaps."""
    recommendations = []
    for gap in skill_gaps[:5]:  # Limit to top 5 to avoid API quota burn
        resources = await search_youtube(gap.get("skill", ""))
        recommendations.append({
            "skill": gap.get("skill", ""),
            "priority": gap.get("priority", 99),
            "why": gap.get("why", ""),
            "playlists": [r for r in resources if r["type"] == "playlist"][:2],
            "videos": [r for r in resources if r["type"] == "video"][:2],
        })
    return recommendations
