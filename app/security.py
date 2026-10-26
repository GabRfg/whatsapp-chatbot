"""
Autenticação do webhook.

Sem isto, `/webhook` é um relay aberto: quem alcança a porta faz o número enviar
mensagens para qualquer destino. A verificação acontece antes de qualquer
processamento do corpo.
"""
from __future__ import annotations

import hashlib
import hmac

from .config import Settings


class WebhookAuthError(Exception):
    """A requisição não comprovou vir do provedor."""


def _constante(a: str, b: str) -> bool:
    # compare_digest evita que o tempo de resposta revele quantos caracteres
    # do segredo foram acertados.
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify(settings: Settings, headers: dict[str, str], raw_body: bytes) -> None:
    """
    Valida a origem da requisição. Levanta WebhookAuthError se não confere.

    Aceita duas provas, porque as versões da WAHA diferem no que oferecem:

    1. Token fixo num cabeçalho — simples, e suficiente quando o canal é HTTPS
       ou uma rede interna fechada.
    2. HMAC-SHA256 sobre o corpo cru — mais forte: além da origem, prova que o
       corpo não foi alterado no caminho.

    O corpo precisa ser os bytes exatos recebidos. Serializar o JSON de novo
    para conferir o HMAC produz bytes diferentes e a verificação falha.
    """
    if not settings.webhook_secret:
        if settings.allow_insecure_webhook:
            return
        raise WebhookAuthError("webhook sem segredo configurado")

    # Cabeçalhos HTTP não diferenciam maiúsculas de minúsculas.
    normalizados = {k.lower(): v for k, v in headers.items()}

    token = normalizados.get(settings.webhook_token_header.lower())
    if token and _constante(token, settings.webhook_secret):
        return

    assinatura = normalizados.get(settings.webhook_hmac_header.lower())
    if assinatura:
        esperado = hmac.new(
            settings.webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        # Algumas implementações prefixam o algoritmo.
        recebido = assinatura.split("=", 1)[-1].strip().lower()
        if _constante(recebido, esperado):
            return

    raise WebhookAuthError("token ou assinatura do webhook inválidos")
