"""Content analyzer for Horizon.

Analyzes content items using AI and assigns importance scores.
"""

import asyncio
import json
import re
from typing import List, Optional

from ..models import ContentItem
from ..ai.client import AIClient
from ..ai.prompts import CONTENT_ANALYSIS_SYSTEM, CONTENT_ANALYSIS_USER


class ContentAnalyzer:
    """Analyzes content items using AI to assign importance scores and categories."""

    def __init__(self, ai_client: AIClient):
        self.client = ai_client

    async def analyze_batch(self, items: List[ContentItem]) -> List[ContentItem]:
        """Analyze a batch of content items concurrently.

        Args:
            items: Content items to analyze

        Returns:
            List[ContentItem]: Analyzed items with AI scores and summaries
        """
        if not items:
            return items

        semaphore = asyncio.Semaphore(self.client.config.get("analysis_concurrency", 3))

        async def analyze_with_throttle(item: ContentItem) -> ContentItem:
            throttle = self.client.config.get("throttle_sec", 0)
            if throttle > 0:
                await asyncio.sleep(throttle)
            async with semaphore:
                try:
                    await self._analyze_item(item)
                except Exception as e:
                    item.ai_score = 0.0
                    item.ai_reason = "Analysis failed"
                    item.ai_summary = item.title
                    item.ai_tags = []
                return item

        coros = []
        for item in items:
            coros.append(analyze_with_throttle(item))

        analyzed_items = await asyncio.gather(*coros)

        return analyzed_items

    async def _analyze_item(self, item: ContentItem) -> None:
        """Analyze a single content item.

        Args:
            item: Content item to analyze (modified in-place)
        """
        # Prepare content section
        content_section = ""
        if item.content:
            # Split off comments if present
            content_text = item.content
            if "--- Top Comments ---" in content_text:
                main, comments_part = content_text.split("--- Top Comments ---", 1)
                content_section = f"Content: {main.strip()[:800]}"
            else:
                content_section = f"Content: {content_text[:1000]}"

        # Prepare discussion section (comments, engagement)
        discussion_parts = []
        if item.content and "--- Top Comments ---" in item.content:
            comments_part = item.content.split("--- Top Comments ---", 1)[1]
            discussion_parts.append(f"Community Comments:\n{comments_part[:1500]}")

        meta = item.metadata
        engagement_items = []
        if meta.get("score"):
            engagement_items.append(f"score: {meta['score']}")
        if meta.get("descendants"):
            engagement_items.append(f"{meta['descendants']} comments")
        if meta.get("favorite_count"):
            engagement_items.append(f"{meta['favorite_count']} likes")
        if meta.get("retweet_count"):
            engagement_items.append(f"{meta['retweet_count']} retweets")
        if meta.get("reply_count"):
            engagement_items.append(f"{meta['reply_count']} replies")
        if meta.get("views"):
            engagement_items.append(f"{meta['views']} views")
        if meta.get("bookmarks"):
            engagement_items.append(f"{meta['bookmarks']} bookmarks")
        if meta.get("upvote_ratio"):
            engagement_items.append(f"upvote ratio: {meta['upvote_ratio']:.0%}")
        if engagement_items:
            discussion_parts.append(f"Engagement: {', '.join(engagement_items)}")
        if meta.get("discussion_url"):
            discussion_parts.append(f"Discussion: {meta['discussion_url']}")
        if meta.get("community_note"):
            discussion_parts.append(f"Community Note: {meta['community_note']}")

        discussion_section = "\n".join(discussion_parts) if discussion_parts else ""

        # Generate user prompt
        user_prompt = CONTENT_ANALYSIS_USER.format(
            title=item.title,
            source=f"{item.source_type.value}",
            author=item.author or "Unknown",
            url=str(item.url),
            content_section=content_section,
            discussion_section=discussion_section
        )

        # Get AI completion
        response = await self.client.complete(
            system=CONTENT_ANALYSIS_SYSTEM,
            user=user_prompt,
        )

        # Parse JSON response with robust fallback
        result = self._parse_json_response(response)
        if result is None:
            print(f"Warning: could not parse analysis response for {item.id}, using defaults")
            item.ai_score = 0.0
            item.ai_reason = "Analysis response parse failed"
            item.ai_summary = item.title
            item.ai_tags = []
            return

        # Update item with analysis results
        item.ai_score = float(result.get("score", 0))
        item.ai_reason = result.get("reason", "")
        item.ai_summary = result.get("summary_zh") or result.get("summary", item.title)
        item.ai_tags = result.get("tags", [])

    def _parse_json_response(self, response: str) -> Optional[dict]:
        """Parse JSON response from AI with robust fallback.

        Handles:
        - Proper JSON
        - JSON within code fences (```json ... ```)
        - Truncated JSON (attempts to complete it)
        - Extra text after JSON

        Args:
            response: Raw text response from AI

        Returns:
            Parsed JSON dict or None if parsing failed
        """
        if not response:
            return None

        # Try to extract JSON from code fences
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            json_str = response.strip()

        # Try direct parsing
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # Try finding the JSON object in the response
        brace_start = json_str.find("{")
        brace_end = json_str.rfind("}")

        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            try:
                return json.loads(json_str[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                pass

        # Try to repair truncated JSON by adding closing braces
        if "{" in json_str:
            # Count braces to determine if we need to close
            open_braces = json_str.count("{")
            close_braces = json_str.count("}")

            if open_braces > close_braces:
                # Add missing closing braces
                repaired = json_str + "}" * (open_braces - close_braces)
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass

                # Try extracting from brace start with repair
                if brace_start != -1:
                    partial = json_str[brace_start:]
                    open_b = partial.count("{")
                    close_b = partial.count("}")
                    if open_b > close_b:
                        repaired = partial + "}" * (open_b - close_b)
                        try:
                            return json.loads(repaired)
                        except json.JSONDecodeError:
                            pass

        return None
