"""
Testes do motor.

O motor é uma função pura: nenhum Redis, nenhum HTTP, nenhum WhatsApp. É por
isso que o fluxo inteiro pode ser exercitado em milissegundos.
"""
import pytest

from app.engine import decide
from app.flow import HANDOFF, Flow

FLUXO = Flow.model_validate(
    {
        "version": 1,
        "bot": {
            "name": "Teste",
            "entrypoint": "menu",
            "reset_keywords": ["menu", "0"],
            "back_hint": " [voltar]",
            "fallback": "Não entendi.",
            "unsupported_media": "Só leio texto.",
        },
        "states": {
            "menu": {
                "text": "MENU",
                "options": {
                    "1": {"text": "Fale conosco", "next": "handoff"},
                    "2": {"text": "Link: exemplo.com", "next": "menu"},
                    "3": {"next": "sub"},
                },
            },
            "sub": {
                "text": "SUBMENU",
                "options": {
                    "3.1": {"text": "Resposta A", "next": "handoff"},
                    "3.2": {"text": "Resposta B", "next": "menu"},
                },
            },
        },
    }
)


def test_usuario_novo_recebe_o_menu():
    d = decide(FLUXO, None, "oi")
    assert d.replies == ["MENU"]
    assert d.next_state == "menu"


def test_menu_principal_nao_recebe_a_dica_de_voltar():
    d = decide(FLUXO, None, "oi")
    assert "[voltar]" not in d.replies[0]


def test_opcao_que_entrega_ao_humano():
    d = decide(FLUXO, "menu", "1")
    assert d.replies == ["Fale conosco [voltar]"]
    assert d.next_state == HANDOFF


def test_opcao_informativa_nao_reenvia_o_menu():
    # Reenviar o menu inteiro depois de cada resposta curta polui a conversa.
    d = decide(FLUXO, "menu", "2")
    assert d.replies == ["Link: exemplo.com [voltar]"]
    assert d.next_state == "menu"


def test_opcao_que_abre_submenu_envia_o_texto_do_submenu():
    d = decide(FLUXO, "menu", "3")
    assert d.replies == ["SUBMENU [voltar]"]
    assert d.next_state == "sub"


def test_submenu_responde_as_proprias_opcoes():
    d = decide(FLUXO, "sub", "3.1")
    assert d.replies == ["Resposta A [voltar]"]
    assert d.next_state == HANDOFF


def test_opcao_de_outro_estado_nao_vaza_para_o_submenu():
    # Estando no submenu, "1" não é uma opção válida: deve cair no fallback,
    # e não executar a opção "1" do menu principal.
    d = decide(FLUXO, "sub", "1")
    assert d.replies == ["Não entendi."]
    assert d.next_state == "sub"


def test_handoff_silencia_o_bot():
    d = decide(FLUXO, HANDOFF, "minha reclamação detalhada")
    assert d.replies == []
    assert d.next_state is None


def test_reset_tira_do_handoff():
    d = decide(FLUXO, HANDOFF, "menu")
    assert d.replies == ["MENU"]
    assert d.next_state == "menu"


@pytest.mark.parametrize("palavra", ["menu", "MENU", "  Menu  ", "0"])
def test_reset_funciona_em_qualquer_caixa(palavra):
    assert decide(FLUXO, "sub", palavra).next_state == "menu"


def test_midia_responde_em_vez_de_quebrar():
    # Era o bug que devolvia 500 a cada foto ou áudio recebido.
    d = decide(FLUXO, "menu", None)
    assert d.replies == ["Só leio texto."]
    assert d.next_state is None


def test_midia_durante_handoff_nao_interrompe():
    # Um atendente humano está conversando: o bot não entra na frente.
    d = decide(FLUXO, HANDOFF, None)
    assert d.replies == []


def test_midia_de_usuario_novo_nao_quebra():
    d = decide(FLUXO, None, None)
    assert d.replies == ["Só leio texto."]


def test_texto_sem_correspondencia_cai_no_fallback():
    d = decide(FLUXO, "menu", "bom dia, tudo bem?")
    assert d.replies == ["Não entendi."]
    assert d.next_state == "menu"


def test_estado_removido_do_yaml_nao_prende_o_usuario():
    # Um usuário pode ter no Redis o nome de um estado que o YAML já não tem.
    d = decide(FLUXO, "estado_de_uma_versao_antiga", "qualquer coisa")
    assert d.next_state == "menu"
    assert d.replies == ["MENU"]


def test_opcoes_sao_comparadas_sem_caixa_e_sem_espacos():
    assert decide(FLUXO, "sub", " 3.2 ").next_state == "menu"


def test_mensagem_vazia_cai_no_fallback():
    d = decide(FLUXO, "menu", "   ")
    assert d.replies == ["Não entendi."]
