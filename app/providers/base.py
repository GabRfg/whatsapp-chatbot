"""
Contrato do provedor de mensagens.

O motor não sabe o que é WAHA. Trocar para Twilio, para a Cloud API da Meta ou
para um provedor de testes é escrever outra classe com estes dois métodos.
"""
from __future__ import annotations

from typing import Protocol


class MessageProvider(Protocol):
    async def send_text(self, chat_id: str, text: str) -> bool:
        """Envia uma mensagem de texto. Devolve se foi aceita pelo provedor."""
        ...

    async def close(self) -> None:
        ...
