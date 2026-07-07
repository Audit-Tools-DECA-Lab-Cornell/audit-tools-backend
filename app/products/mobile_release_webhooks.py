from __future__ import annotations

import json
from typing import TypeAlias

from fastapi import HTTPException, Request, Response, status

from app.products.mobile_release_config import get_product_release_config
from app.products.mobile_release_eas import record_eas_webhook_payload, verify_eas_webhook_signature
from app.products.mobile_release_models import MobileProduct

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


async def handle_eas_release_webhook(product: MobileProduct, request: Request) -> Response:
	config = get_product_release_config(product)
	secret = config.eas_webhook_secret()
	if secret is None:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="EAS webhook secret is not configured.",
		)

	body = await request.body()
	signature = request.headers.get("expo-signature")
	if not verify_eas_webhook_signature(body, signature, secret):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid EAS webhook signature.")

	try:
		decoded = body.decode("utf-8")
		payload = json.loads(decoded)
	except (UnicodeDecodeError, json.JSONDecodeError) as exc:
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid EAS webhook payload.") from exc

	if not isinstance(payload, dict):
		raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid EAS webhook payload.")

	record_eas_webhook_payload(product, json.dumps(payload))
	return Response(status_code=status.HTTP_204_NO_CONTENT)
