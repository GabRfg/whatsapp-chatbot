"""
Configuração por variáveis de ambiente.

O padrão é falhar fechado: sem segredo de webhook configurado, a aplicação não
sobe. Abrir essa porta exige dizer explicitamente que se quer isso.
"""
from __future__ import annotations

import hashlib

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Fluxo -------------------------------------------------------------
    flow_path: str = Field(default="flows/example.yml", alias="FLOW_PATH")

    # --- Provedor (WAHA) ---------------------------------------------------
    waha_url: str = Field(default="http://waha:3000", alias="WAHA_URL")
    waha_api_key: str | None = Field(default=None, alias="WAHA_API_KEY")
    send_timeout: float = Field(default=15.0, alias="SEND_TIMEOUT")

    # --- Redis -------------------------------------------------------------
    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_password: str | None = Field(default=None, alias="REDIS_PASSWORD")

    # --- Segurança do webhook ---------------------------------------------
    # Segredo compartilhado. A WAHA pode enviá-lo como cabeçalho fixo, ou
    # assinar o corpo com HMAC — as duas formas são aceitas.
    webhook_secret: str | None = Field(default=None, alias="WEBHOOK_SECRET")
    webhook_token_header: str = Field(default="x-webhook-token", alias="WEBHOOK_TOKEN_HEADER")
    webhook_hmac_header: str = Field(default="x-webhook-hmac", alias="WEBHOOK_HMAC_HEADER")

    # Escotilha de escape, para desenvolvimento local. Nomeada de forma que
    # ninguém a ligue em produção sem perceber o que está fazendo.
    allow_insecure_webhook: bool = Field(default=False, alias="ALLOW_INSECURE_WEBHOOK")

    # --- Limites -----------------------------------------------------------
    rate_limit_messages: int = Field(default=20, alias="RATE_LIMIT_MESSAGES")
    rate_limit_window: int = Field(default=60, alias="RATE_LIMIT_WINDOW")
    max_message_length: int = Field(default=4096, alias="MAX_MESSAGE_LENGTH")

    @model_validator(mode="after")
    def _exige_segredo(self) -> "Settings":
        if not self.webhook_secret and not self.allow_insecure_webhook:
            raise ValueError(
                "WEBHOOK_SECRET não definido. Sem ele qualquer pessoa que alcance "
                "a porta do bot pode forjar mensagens e fazer o número enviar "
                "conteúdo arbitrário. Defina WEBHOOK_SECRET, ou "
                "ALLOW_INSECURE_WEBHOOK=true se for ambiente local descartável."
            )
        return self


def mask(chat_id: str) -> str:
    """
    Identificador curto e estável para logs.

    Um chatId é um número de telefone, portanto dado pessoal. Registrar o hash
    permite correlacionar eventos do mesmo usuário sem gravar quem ele é.
    """
    return hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:12]
