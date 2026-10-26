"""
Testes do endpoint.

Cobrem os dois problemas confirmados no bot original: payload de mídia
derrubando o handler com 500, e webhook sem autenticação aceitando mensagens
forjadas de qualquer origem.
"""
import hashlib
import hmac
import json

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.flow import load_flow
from app.providers.base import MessageProvider
from app.state import StateStore

SEGREDO = "segredo-de-teste"


class ProvedorFalso(MessageProvider):
    """Registra os envios em vez de falar com a WAHA."""

    def __init__(self):
        self.enviados: list[tuple[str, str]] = []

    async def send_text(self, chat_id: str, text: str) -> bool:
        self.enviados.append((chat_id, text))
        return True

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        pass


@pytest.fixture
def ambiente(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setenv("FLOW_PATH", "flows/example.yml")
    monkeypatch.setenv("ALLOW_INSECURE_WEBHOOK", "false")

    from app.main import app

    provedor = ProvedorFalso()
    settings = Settings()

    flow = load_flow(settings.flow_path)

    # O lifespan roda ao entrar no contexto e monta as dependências reais;
    # a substituição precisa vir depois dele, ou é sobrescrita.
    with TestClient(app) as client:
        client.app.state.settings = settings
        client.app.state.flow = flow
        client.app.state.provider = provedor
        client.app.state.store = StateStore(
            fakeredis.aioredis.FakeRedis(decode_responses=True), flow.bot.state_ttl
        )
        yield client, provedor


def assina(corpo: bytes) -> str:
    return hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()


def post(client, payload, *, token=SEGREDO, hmac_sig=None):
    corpo = json.dumps(payload).encode()
    headers = {"content-type": "application/json"}
    if token:
        headers["x-webhook-token"] = token
    if hmac_sig:
        headers["x-webhook-hmac"] = hmac_sig
    return client.post("/webhook", content=corpo, headers=headers)


def msg(texto, chat="5541999999999@c.us", **extra):
    payload = {"from": chat, "fromMe": False, **extra}
    if texto is not None:
        payload["body"] = texto
    return {"event": "message", "payload": payload}


# --- autenticação ----------------------------------------------------------

def test_sem_token_e_recusado(ambiente):
    client, provedor = ambiente
    r = post(client, msg("menu"), token=None)
    assert r.status_code == 401
    assert provedor.enviados == [], "nenhuma mensagem deve sair de requisição não autenticada"


def test_token_errado_e_recusado(ambiente):
    client, provedor = ambiente
    assert post(client, msg("menu"), token="chute").status_code == 401
    assert provedor.enviados == []


def test_token_correto_e_aceito(ambiente):
    client, _ = ambiente
    assert post(client, msg("menu")).status_code == 200


def test_hmac_valido_e_aceito(ambiente):
    client, _ = ambiente
    payload = msg("menu")
    corpo = json.dumps(payload).encode()
    r = client.post(
        "/webhook",
        content=corpo,
        headers={"content-type": "application/json", "x-webhook-hmac": assina(corpo)},
    )
    assert r.status_code == 200


def test_hmac_de_outro_corpo_e_recusado(ambiente):
    # Prova que a assinatura cobre o conteúdo, não só a origem.
    client, provedor = ambiente
    corpo_real = json.dumps(msg("menu", chat="5511000000000@c.us")).encode()
    assinatura_de_outro = assina(json.dumps(msg("menu")).encode())
    r = client.post(
        "/webhook",
        content=corpo_real,
        headers={"content-type": "application/json", "x-webhook-hmac": assinatura_de_outro},
    )
    assert r.status_code == 401
    assert provedor.enviados == []


def test_hmac_com_prefixo_de_algoritmo(ambiente):
    client, _ = ambiente
    corpo = json.dumps(msg("menu")).encode()
    r = client.post(
        "/webhook",
        content=corpo,
        headers={"content-type": "application/json", "x-webhook-hmac": f"sha256={assina(corpo)}"},
    )
    assert r.status_code == 200


# --- robustez de payload ---------------------------------------------------

@pytest.mark.parametrize(
    "payload,descricao",
    [
        ({"event": "message", "payload": {"from": "x@c.us", "fromMe": False}}, "sem body (mídia)"),
        ({"event": "message", "payload": {"from": "x@c.us", "body": None}}, "body nulo"),
        ({"event": "message", "payload": {"from": "x@c.us", "body": 123}}, "body numérico"),
        ({"event": "message", "payload": {"from": "x@c.us", "body": {"a": 1}}}, "body objeto"),
        ({"event": "message", "payload": {"from": "x@c.us", "body": True}}, "body booleano"),
    ],
)
def test_mensagem_sem_texto_nao_derruba(ambiente, payload, descricao):
    # No bot original cada um destes devolvia 500, e a WAHA ainda tentava 2x.
    client, provedor = ambiente
    r = post(client, payload)
    assert r.status_code == 200, descricao
    assert any("texto" in t for _, t in provedor.enviados), descricao


@pytest.mark.parametrize(
    "payload",
    [
        {"event": "message"},
        {"event": "message", "payload": {}},
        {"event": "message", "payload": {"body": "menu"}},
        {},
        {"event": "message", "payload": []},
    ],
)
def test_payload_malformado_vira_422_e_nao_500(ambiente, payload):
    client, _ = ambiente
    assert post(client, payload).status_code in (200, 422)


def test_json_invalido_vira_422(ambiente):
    client, _ = ambiente
    r = client.post(
        "/webhook",
        content=b"{isso nao e json",
        headers={"content-type": "application/json", "x-webhook-token": SEGREDO},
    )
    assert r.status_code == 422


def test_mensagem_propria_e_ignorada(ambiente):
    client, provedor = ambiente
    r = post(client, {"event": "message", "payload": {"from": "x@c.us", "body": "oi", "fromMe": True}})
    assert r.status_code == 200
    assert provedor.enviados == [], "responder à própria mensagem criaria um laço"


def test_evento_que_nao_e_mensagem_e_ignorado(ambiente):
    client, provedor = ambiente
    r = post(client, {"event": "session.status", "payload": {"from": "x@c.us"}})
    assert r.status_code == 200
    assert provedor.enviados == []


def test_mensagem_gigante_nao_estoura(ambiente):
    client, _ = ambiente
    r = post(client, msg("a" * 200_000))
    assert r.status_code == 200


# --- conversa --------------------------------------------------------------

def test_conversa_completa(ambiente):
    client, provedor = ambiente
    chat = "5541911112222@c.us"

    post(client, msg("oi", chat=chat))
    assert "Acme" in provedor.enviados[-1][1]

    provedor.enviados.clear()
    post(client, msg("4", chat=chat))
    assert "pedidos" in provedor.enviados[-1][1].lower()

    provedor.enviados.clear()
    post(client, msg("4.1", chat=chat))
    assert "pedido" in provedor.enviados[-1][1].lower()

    # Agora em handoff: o bot não responde mais.
    provedor.enviados.clear()
    post(client, msg("o pedido é 12345 e não chegou", chat=chat))
    assert provedor.enviados == []

    # Mídia durante o handoff também não interrompe.
    post(client, msg(None, chat=chat))
    assert provedor.enviados == []

    # A palavra de reinício traz de volta.
    post(client, msg("menu", chat=chat))
    assert "Acme" in provedor.enviados[-1][1]


def test_estado_e_por_usuario(ambiente):
    client, provedor = ambiente
    a, b = "5541900000001@c.us", "5541900000002@c.us"

    post(client, msg("menu", chat=a))
    post(client, msg("4", chat=a))  # A entra no submenu

    # B nunca entrou no submenu: "4.1" não pode acionar a opção de lá.
    # Como B é um usuário novo, o certo é receber o menu — e não o fallback,
    # que seria confuso para quem ainda não viu opção nenhuma.
    provedor.enviados.clear()
    post(client, msg("4.1", chat=b))
    resposta_b = provedor.enviados[-1][1]
    assert "Bem-vindo" in resposta_b
    assert "data prevista" not in resposta_b, "B não pode receber a resposta do submenu de A"

    # E o estado de A continua intacto: ele ainda está no submenu.
    provedor.enviados.clear()
    post(client, msg("4.1", chat=a))
    assert "data prevista" in provedor.enviados[-1][1]


# --- limites ---------------------------------------------------------------

def test_rate_limit_corta_enxurrada(ambiente, monkeypatch):
    client, provedor = ambiente
    client.app.state.settings.rate_limit_messages = 3
    chat = "5541933334444@c.us"

    codigos = [post(client, msg("menu", chat=chat)).status_code for _ in range(6)]
    assert codigos[:3] == [200, 200, 200]
    assert 429 in codigos[3:]


def test_rate_limit_e_por_usuario(ambiente):
    client, _ = ambiente
    client.app.state.settings.rate_limit_messages = 2
    for _ in range(4):
        post(client, msg("menu", chat="5541955556666@c.us"))
    # Outro número não deve ser afetado.
    assert post(client, msg("menu", chat="5541977778888@c.us")).status_code == 200


# --- configuração ----------------------------------------------------------

def test_sem_segredo_a_aplicacao_recusa_subir(monkeypatch):
    # Falha fechada: é a diferença entre um bot e um relay aberto.
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ALLOW_INSECURE_WEBHOOK", "false")
    with pytest.raises(Exception, match="WEBHOOK_SECRET"):
        Settings(_env_file=None)


def test_escotilha_explicita_permite_subir_sem_segredo(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ALLOW_INSECURE_WEBHOOK", "true")
    s = Settings(_env_file=None)
    assert s.allow_insecure_webhook is True


# --- resiliência -----------------------------------------------------------

def test_redis_fora_do_ar_devolve_503_e_nao_500(ambiente, monkeypatch):
    # Com o estado indisponível, o certo é pedir reenvio ao provedor — nunca
    # estourar um 500 com stack trace na resposta.
    from redis.exceptions import ConnectionError as RedisConnectionError

    client, provedor = ambiente

    async def cai(*a, **k):
        raise RedisConnectionError("redis fora do ar")

    monkeypatch.setattr(client.app.state.store, "allow", cai)

    r = post(client, msg("menu"))
    assert r.status_code == 503
    assert "Traceback" not in r.text
    assert provedor.enviados == []


def test_falha_do_redis_na_leitura_de_estado_tambem_e_tratada(ambiente, monkeypatch):
    from redis.exceptions import TimeoutError as RedisTimeoutError

    client, _ = ambiente

    async def cai(*a, **k):
        raise RedisTimeoutError("tempo esgotado")

    monkeypatch.setattr(client.app.state.store, "get", cai)
    assert post(client, msg("menu")).status_code == 503
