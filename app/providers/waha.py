"""Provedor WAHA (https://waha.devlike.pro)."""
from __future__ import annotations

import logging

import httpx

from ..config import Settings, mask

logger = logging.getLogger(__name__)


class WahaProvider:
    def __init__(self, settings: Settings, session: str):
        self.base_url = settings.waha_url.rstrip("/")
        self.session = session
        self.headers = {"X-Api-Key": settings.waha_api_key} if settings.waha_api_key else {}
        # Um cliente reaproveitado mantém as conexões abertas. Criar um
        # AsyncClient por mensagem refaz o handshake TCP a cada envio.
        self.client = httpx.AsyncClient(timeout=settings.send_timeout)

    async def send_text(self, chat_id: str, text: str) -> bool:
        payload = {"chatId": chat_id, "text": text, "session": self.session}
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/sendText", json=payload, headers=self.headers
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            # O corpo da resposta de erro pode ecoar o texto enviado, então
            # registra-se apenas o código.
            logger.error(
                "envio recusado pelo provedor (status %s) para %s",
                exc.response.status_code,
                mask(chat_id),
            )
        except httpx.HTTPError as exc:
            logger.error("falha de rede ao enviar para %s: %s", mask(chat_id), type(exc).__name__)
        return False

    async def health(self) -> bool:
        try:
            resp = await self.client.get(f"{self.base_url}/api/sessions", headers=self.headers)
            return resp.status_code < 500
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self.client.aclose()
