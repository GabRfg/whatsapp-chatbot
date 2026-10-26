"""
Motor da conversa.

Decide o que responder e para qual estado ir, sem saber nada sobre WhatsApp,
Redis ou HTTP. É uma função pura sobre (estado atual, mensagem, fluxo), o que a
torna testável sem subir infraestrutura nenhuma.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .flow import HANDOFF, Flow

# Estado implícito de quem nunca falou com o bot.
START = "__start__"


@dataclass
class Decision:
    """O que o motor decidiu: textos a enviar e para onde mover o usuário."""

    replies: list[str] = field(default_factory=list)
    next_state: str | None = None
    # TTL deve ser renovado mesmo quando o estado não muda (usuário ativo).
    touch: bool = True
    reason: str = "ok"


def _compose(flow: Flow, texto: str | None, *, hint: bool) -> str | None:
    if not texto:
        return None
    if hint and flow.bot.back_hint:
        return texto + flow.bot.back_hint
    return texto


def _enter(flow: Flow, nome: str) -> Decision:
    """Entra num estado: envia o texto dele e registra a transição."""
    if nome == HANDOFF:
        return Decision(next_state=HANDOFF, reason="handoff")

    estado = flow.state(nome)
    if estado is None:
        # O validador do fluxo impede isso; a guarda existe para o caso de o
        # Redis trazer um estado de uma versão anterior do YAML.
        return Decision(
            replies=[flow.state(flow.bot.entrypoint).text or ""],
            next_state=flow.bot.entrypoint,
            reason="estado_desconhecido",
        )

    if estado.handoff:
        return Decision(next_state=nome, reason="handoff")

    texto = _compose(flow, estado.text, hint=nome != flow.bot.entrypoint)
    return Decision(
        replies=[texto] if texto else [],
        next_state=nome,
        reason="entrou_em_estado",
    )


def decide(flow: Flow, current_state: str | None, message: str | None) -> Decision:
    """
    Resolve uma mensagem recebida.

    :param current_state: estado guardado, ou None para usuário novo
    :param message: texto da mensagem, ou None quando não há texto (mídia)
    """
    estado_atual = current_state or START

    # 1. Mensagem sem texto: áudio, imagem, figurinha, localização, contato.
    #    Responder em vez de quebrar é a diferença entre um bot e um 500.
    if message is None:
        if estado_atual == HANDOFF or (
            (e := flow.state(estado_atual)) is not None and e.handoff
        ):
            # Em atendimento humano o bot não interrompe, nem para avisar.
            return Decision(next_state=None, reason="handoff_silencioso")
        return Decision(
            replies=[flow.bot.unsupported_media],
            next_state=None,
            reason="midia_sem_texto",
        )

    texto = message.strip().lower()

    # 2. Palavra de reinício: funciona de qualquer lugar, inclusive do handoff.
    #    É a única saída do atendimento humano, por isso vem antes de tudo.
    if flow.is_reset(texto):
        return _enter(flow, flow.bot.entrypoint)

    # 3. Handoff: o bot fica em silêncio até um reinício.
    estado = flow.state(estado_atual)
    if estado_atual == HANDOFF or (estado is not None and estado.handoff):
        return Decision(next_state=None, reason="handoff_silencioso")

    # 4. Usuário novo, ou estado que sumiu do YAML: manda para a entrada.
    if estado is None:
        return _enter(flow, flow.bot.entrypoint)

    # 5. A mensagem corresponde a alguma opção do estado atual?
    opcao = estado.options.get(texto)
    if opcao is not None:
        decisao = Decision(reason="opcao")

        resposta = _compose(flow, opcao.text, hint=True)
        if resposta:
            decisao.replies.append(resposta)

        destino = opcao.next or estado_atual
        if destino == estado_atual:
            # Opção informativa: responde e mantém o usuário onde está, sem
            # reenviar o menu que ele acabou de ler.
            decisao.next_state = estado_atual
        else:
            seguinte = _enter(flow, destino)
            decisao.replies.extend(seguinte.replies)
            decisao.next_state = seguinte.next_state

        return decisao

    # 6. Nada correspondeu.
    return Decision(
        replies=[_compose(flow, flow.bot.fallback, hint=False) or ""],
        next_state=estado_atual,
        reason="fallback",
    )
