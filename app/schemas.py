"""
Formato do webhook recebido.

Validar com Pydantic transforma payload inesperado em 422, não em 500. É o que
separa "o bot ignorou uma mensagem estranha" de "o bot caiu e o provedor tentou
de novo três vezes".
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class MessagePayload(BaseModel):
    model_config = {"extra": "ignore"}

    chat_id: str = Field(alias="from", min_length=1, max_length=128)
    body: str | None = None
    from_me: bool = Field(default=False, alias="fromMe")

    @field_validator("body", mode="before")
    @classmethod
    def _so_texto(cls, v: Any) -> str | None:
        """
        Mensagens de mídia chegam sem `body`, com `body` nulo, ou com um valor
        que não é string. Todos viram None, e o motor trata isso como "sem
        texto" em vez de estourar.
        """
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, str):
            return v or None
        return None


class WebhookEvent(BaseModel):
    model_config = {"extra": "ignore"}

    event: str
    payload: MessagePayload | None = None
