"""Validação do fluxo: o que o motor aceita e o que ele recusa na subida."""
import textwrap

import pytest
import yaml
from pydantic import ValidationError

from app.flow import Flow, load_flow

MINIMO = {
    "version": 1,
    "bot": {"entrypoint": "menu"},
    "states": {"menu": {"text": "Oi", "options": {"1": {"text": "a", "next": "handoff"}}}},
}


def test_fluxo_minimo_valido():
    flow = Flow.model_validate(MINIMO)
    assert flow.bot.entrypoint == "menu"
    assert "menu" in flow.states


def test_exemplo_do_repositorio_e_valido():
    flow = load_flow("flows/example.yml")
    assert flow.states
    assert flow.bot.entrypoint in flow.states


def test_next_apontando_para_estado_inexistente_e_recusado():
    dados = {**MINIMO, "states": {"menu": {"text": "Oi", "options": {"1": {"next": "nao_existe"}}}}}
    with pytest.raises(ValidationError, match="não corresponde a nenhum estado"):
        Flow.model_validate(dados)


def test_entrypoint_inexistente_e_recusado():
    dados = {**MINIMO, "bot": {"entrypoint": "inicio"}}
    with pytest.raises(ValidationError, match="entrypoint"):
        Flow.model_validate(dados)


def test_estado_orfao_e_recusado():
    dados = {
        "version": 1,
        "bot": {"entrypoint": "menu"},
        "states": {
            "menu": {"text": "Oi", "options": {"1": {"text": "a"}}},
            "perdido": {"text": "ninguem chega aqui"},
        },
    }
    with pytest.raises(ValidationError, match="inalcançáveis"):
        Flow.model_validate(dados)


def test_nome_reservado_nao_pode_ser_redefinido():
    dados = {
        "version": 1,
        "bot": {"entrypoint": "menu"},
        "states": {
            "menu": {"text": "Oi", "options": {"1": {"next": "handoff"}}},
            "handoff": {"text": "meu proprio"},
        },
    }
    with pytest.raises(ValidationError, match="reservados"):
        Flow.model_validate(dados)


def test_campo_desconhecido_e_recusado():
    # Erro de digitação em chave de configuração falha na subida, em vez de ser
    # silenciosamente ignorado e render comportamento inesperado em produção.
    dados = {**MINIMO, "states": {"menu": {"txt": "Oi"}}}
    with pytest.raises(ValidationError):
        Flow.model_validate(dados)


def test_estado_sem_conteudo_e_recusado():
    dados = {**MINIMO, "states": {"menu": {}}}
    with pytest.raises(ValidationError):
        Flow.model_validate(dados)


def test_opcao_sem_texto_e_sem_next_e_recusada():
    dados = {**MINIMO, "states": {"menu": {"text": "Oi", "options": {"1": {}}}}}
    with pytest.raises(ValidationError):
        Flow.model_validate(dados)


def test_reset_keywords_vazio_e_recusado():
    # Sem palavra de reinício não há saída do atendimento humano.
    dados = {**MINIMO, "bot": {"entrypoint": "menu", "reset_keywords": []}}
    with pytest.raises(ValidationError, match="reset_keywords"):
        Flow.model_validate(dados)


def test_reset_keywords_sao_normalizadas():
    dados = {**MINIMO, "bot": {"entrypoint": "menu", "reset_keywords": ["  MENU  ", "Início"]}}
    flow = Flow.model_validate(dados)
    assert flow.bot.reset_keywords == ["menu", "início"]
    assert flow.is_reset("  MeNu ")


def test_ttl_fora_de_faixa_e_recusado():
    with pytest.raises(ValidationError):
        Flow.model_validate({**MINIMO, "bot": {"entrypoint": "menu", "state_ttl": 5}})


def test_yaml_sem_safe_load_nao_constroi_objetos(tmp_path):
    # Uma tag de construção do PyYAML não deve virar execução de código.
    arquivo = tmp_path / "mal.yml"
    arquivo.write_text(
        textwrap.dedent(
            """
            version: 1
            states: !!python/object/apply:os.system ['echo invadido']
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(yaml.YAMLError):
        load_flow(arquivo)


def test_arquivo_inexistente_da_erro_claro():
    with pytest.raises(FileNotFoundError):
        load_flow("flows/nao_existe.yml")
