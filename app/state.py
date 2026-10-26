"""
Estado por usuário e limitador de taxa, ambos no Redis.

Guarda-se apenas o nome do estado e um contador — nunca o conteúdo das
mensagens. Num canal que recebe denúncias e reclamações, não persistir o que o
usuário escreve é a decisão de projeto mais importante do módulo.
"""
from __future__ import annotations

import redis.asyncio as redis

from .config import Settings


class StateStore:
    def __init__(self, client: redis.Redis, ttl: int):
        self.redis = client
        self.ttl = ttl

    @classmethod
    def from_settings(cls, settings: Settings, ttl: int) -> "StateStore":
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            decode_responses=True,
        )
        return cls(client, ttl)

    @staticmethod
    def _key(chat_id: str) -> str:
        return f"state:{chat_id}"

    async def get(self, chat_id: str) -> str | None:
        return await self.redis.get(self._key(chat_id))

    async def set(self, chat_id: str, estado: str) -> None:
        await self.redis.set(self._key(chat_id), estado, ex=self.ttl)

    async def touch(self, chat_id: str) -> None:
        """Renova o TTL sem alterar o estado. Usuário ativo não expira."""
        await self.redis.expire(self._key(chat_id), self.ttl)

    async def clear(self, chat_id: str) -> None:
        await self.redis.delete(self._key(chat_id))

    async def allow(self, chat_id: str, limite: int, janela: int) -> bool:
        """
        Janela fixa por usuário. Impede que um único número gere envios sem fim.

        INCR e EXPIRE vão no mesmo pipeline: sem isso, uma falha entre os dois
        deixaria uma chave sem expiração, e o usuário ficaria bloqueado para
        sempre.
        """
        chave = f"rate:{chat_id}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(chave)
            pipe.expire(chave, janela)
            atual, _ = await pipe.execute()
        return int(atual) <= limite

    async def ping(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self.redis.aclose()
