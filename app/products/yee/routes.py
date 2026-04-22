"""
YEE product routes.

This module intentionally stays isolated from Playspace logic so YEE can evolve
independently in this folder.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["yee"])


@router.get("/status")
async def get_yee_status() -> dict[str, str]:
	"""Simple product status endpoint for the YEE namespace."""

	return {
		"status": "ok",
		"product": "yee",
		"message": "YEE routes are isolated. Implement YEE-specific APIs in app/products/yee/.",
	}
