"""
Carregamento e validação do fluxo conversacional.

O fluxo é um arquivo YAML: o conteúdo do bot vive ali, não no código. Trocar de
empresa é trocar o YAML. Este módulo transforma o arquivo em objetos validados e
recusa fluxos inconsistentes na inicialização, não em produção com o usuário
esperando.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

# Estados com significado especial para o motor. Não precisam ser declarados no
# YAML e não podem ser redefinidos.
HANDOFF = "handoff"
RESERVED_STATES = {HANDOFF}


class Option(BaseModel):
    """Uma escolha dentro de um estado, acionada pelo texto que o usuário digita."""

    model_config = {"extra": "forbid"}

    label: str | None = Field(
        default=None,
        description="Rótulo opcional, usado só para documentar o fluxo.",
    )
    text: str | None = Field(
        default=None,
        description="Resposta enviada ao escolher esta opção.",
    )
    next: str = Field(
        default="",
        description="Estado seguinte. Vazio mantém o usuário onde está.",
    )

    @model_validator(mode="after")
    def _precisa_fazer_algo(self) -> "Option":
        if not self.text and not self.next:
            raise ValueError("a opção precisa de 'text', de 'next', ou de ambos")
        return self


class State(BaseModel):
    """
    Um ponto do fluxo.

    Um estado com `options` é um menu: ao entrar nele o bot envia `text` e passa a
    aceitar as chaves de `options`. Um estado com `handoff: true` silencia o bot
    até que o usuário digite uma palavra de reinício.
    """

    model_config = {"extra": "forbid"}

    text: str | None = None
    options: dict[str, Option] = Field(default_factory=dict)
    handoff: bool = False

    @model_validator(mode="after")
    def _precisa_de_conteudo(self) -> "State":
        if not self.handoff and not self.text and not self.options:
            raise ValueError("o estado precisa de 'text', de 'options', ou de 'handoff'")
        return self


class BotConfig(BaseModel):
    """Parâmetros de comportamento, independentes do conteúdo."""

    model_config = {"extra": "forbid"}

    name: str = "Bot"
    session: str = "default"
    entrypoint: str = "menu"
    state_ttl: int = Field(default=3600, ge=60, le=86_400)
    reset_keywords: list[str] = Field(default_factory=lambda: ["menu", "iniciar", "0"])
    back_hint: str = ""
    fallback: str = "Não entendi. Digite *menu* para ver as opções."
    unsupported_media: str = (
        "Recebi seu arquivo, mas neste canal só consigo ler texto. "
        "Digite *menu* para ver as opções."
    )

    @model_validator(mode="after")
    def _palavras_normalizadas(self) -> "BotConfig":
        # A comparação acontece sempre em minúsculas e sem espaços nas bordas.
        self.reset_keywords = [k.strip().lower() for k in self.reset_keywords if k.strip()]
        if not self.reset_keywords:
            raise ValueError("reset_keywords não pode ficar vazio: é a única saída do handoff")
        return self


class Flow(BaseModel):
    """O fluxo inteiro, já validado."""

    model_config = {"extra": "forbid"}

    version: Literal[1] = 1
    bot: BotConfig = Field(default_factory=BotConfig)
    states: dict[str, State]

    @model_validator(mode="after")
    def _coerencia(self) -> "Flow":
        nomes = set(self.states)

        reservados_redefinidos = nomes & RESERVED_STATES
        if reservados_redefinidos:
            raise ValueError(
                f"estes nomes são reservados pelo motor: {sorted(reservados_redefinidos)}"
            )

        if self.bot.entrypoint not in nomes:
            raise ValueError(
                f"entrypoint '{self.bot.entrypoint}' não existe em states {sorted(nomes)}"
            )

        # Toda transição precisa apontar para um estado real. Pegar isso aqui
        # evita descobrir o erro de digitação quando um cidadão cai num beco.
        validos = nomes | RESERVED_STATES
        for nome_estado, estado in self.states.items():
            for chave, opcao in estado.options.items():
                if opcao.next and opcao.next not in validos:
                    raise ValueError(
                        f"state '{nome_estado}', opção '{chave}': "
                        f"'next: {opcao.next}' não corresponde a nenhum estado"
                    )

        # Um estado sem texto e sem opções alcançáveis seria um buraco.
        alcancaveis = {self.bot.entrypoint}
        for estado in self.states.values():
            for opcao in estado.options.values():
                if opcao.next:
                    alcancaveis.add(opcao.next)

        orfaos = nomes - alcancaveis
        if orfaos:
            raise ValueError(
                f"estados inalcançáveis (nenhuma opção leva a eles): {sorted(orfaos)}"
            )

        return self

    def state(self, nome: str) -> State | None:
        return self.states.get(nome)

    def is_reset(self, texto: str) -> bool:
        return texto.strip().lower() in self.bot.reset_keywords


def load_flow(path: str | Path) -> Flow:
    """
    Lê e valida um fluxo em YAML.

    `safe_load` é obrigatório aqui: `yaml.load` constrói objetos Python
    arbitrários e transformaria um arquivo de configuração em execução de código.
    """
    caminho = Path(path)
    if not caminho.is_file():
        raise FileNotFoundError(f"fluxo não encontrado: {caminho}")

    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    if not isinstance(dados, dict):
        raise ValueError(f"o fluxo em {caminho} precisa ser um mapeamento YAML")

    return Flow.model_validate(dados)
