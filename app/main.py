"""
Aplicação FastAPI: recebe o webhook, consulta o motor, responde.

Esta camada não contém nenhuma regra de conversa. O que o bot diz está no YAML;
como ele decide está em engine.py. Aqui ficam só HTTP, autenticação e o
acoplamento entre as peças.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request, Response, status
from redis.exceptions import RedisError
from fastapi.responses import JSONResponse

from .config import Settings, mask
from .engine import decide
from .flow import load_flow
from .providers.waha import WahaProvider
from .schemas import WebhookEvent
from .security import WebhookAuthError, verify
from .state import StateStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("chatbot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tudo que pode estar errado na configuração estoura aqui, na subida do
    # container, e não na primeira mensagem de um usuário real.
    settings = Settings()
    flow = load_flow(settings.flow_path)

    app.state.settings = settings
    app.state.flow = flow
    app.state.store = StateStore.from_settings(settings, flow.bot.state_ttl)
    app.state.provider = WahaProvider(settings, flow.bot.session)

    if settings.allow_insecure_webhook and not settings.webhook_secret:
        logger.warning(
            "ALLOW_INSECURE_WEBHOOK ligado: o webhook aceita qualquer origem. "
            "Use apenas em ambiente local descartável."
        )

    logger.info(
        "fluxo '%s' carregado: %d estados, entrada '%s'",
        flow.bot.name,
        len(flow.states),
        flow.bot.entrypoint,
    )
    yield

    await app.state.provider.close()
    await app.state.store.close()


app = FastAPI(title="Configurable WhatsApp Chatbot", version="2.0.0", lifespan=lifespan)


@app.get("/health")
async def health(request: Request):
    """Usado pelo healthcheck do container e por monitoração externa."""
    store: StateStore = request.app.state.store
    provider: WahaProvider = request.app.state.provider

    redis_ok = await store.ping()
    provider_ok = await provider.health()
    ok = redis_ok and provider_ok

    return JSONResponse(
        status_code=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ok" if ok else "degraded",
            "redis": redis_ok,
            "provider": provider_ok,
            "flow": request.app.state.flow.bot.name,
        },
    )


async def _responder(provider: WahaProvider, chat_id: str, textos: list[str]) -> None:
    for texto in textos:
        if texto:
            await provider.send_text(chat_id, texto)


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
    settings: Settings = request.app.state.settings
    flow = request.app.state.flow
    store: StateStore = request.app.state.store
    provider: WahaProvider = request.app.state.provider

    # O corpo cru é necessário para conferir o HMAC: reserializar o JSON
    # produziria bytes diferentes.
    raw = await request.body()

    try:
        verify(settings, dict(request.headers), raw)
    except WebhookAuthError as exc:
        logger.warning("webhook recusado: %s", exc)
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "unauthorized"})

    try:
        evento = WebhookEvent.model_validate_json(raw)
    except Exception:
        # Payload fora do formato esperado não é motivo para erro de servidor.
        return JSONResponse(status_code=422, content={"detail": "invalid payload"})

    if evento.event != "message" or evento.payload is None:
        return JSONResponse(content={"status": "ignored"})

    msg = evento.payload
    if msg.from_me:
        return JSONResponse(content={"status": "ignored_self"})

    chat_id = msg.chat_id

    # Texto muito longo é truncado antes de qualquer comparação: nenhuma opção
    # de menu tem mais que alguns caracteres.
    texto = msg.body[: settings.max_message_length] if msg.body is not None else None

    # Todo acesso ao Redis fica aqui dentro. Sem ele não há estado nem limite,
    # e responder com o menu seria pior que não responder: o usuário perderia
    # o lugar na conversa. 503 faz o provedor tentar de novo, e o stack trace
    # não vaza numa resposta HTTP.
    try:
        if not await store.allow(chat_id, settings.rate_limit_messages, settings.rate_limit_window):
            logger.warning("limite de mensagens atingido por %s", mask(chat_id))
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, content={"status": "rate_limited"}
            )

        estado_atual = await store.get(chat_id)
        decisao = decide(flow, estado_atual, texto)

        if decisao.next_state is not None and decisao.next_state != estado_atual:
            await store.set(chat_id, decisao.next_state)
        elif decisao.touch:
            await store.touch(chat_id)
    except RedisError as exc:
        logger.error("estado indisponível (%s): recusando para o provedor reenviar", type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "state_unavailable"}
        )

    if decisao.replies:
        background_tasks.add_task(_responder, provider, chat_id, decisao.replies)

    logger.info(
        "chat=%s estado=%s -> %s (%s)",
        mask(chat_id),
        estado_atual or "novo",
        decisao.next_state or estado_atual or "novo",
        decisao.reason,
    )

    return JSONResponse(content={"status": "ok", "reason": decisao.reason})
