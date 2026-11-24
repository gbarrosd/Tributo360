"""Registro de versão (backlog, item 6) e painel de versões."""

import pytest

from engines import registro
from engines.contratos import Motor, ResultadoCalculo, Totais, VersaoInfo
from engines.models import VersaoMotor


def _resultado(versao_app="1.7.2", versao_db="V0073"):
    return ResultadoCalculo(
        motor=Motor.REGIME_REGULAR,
        versao=VersaoInfo(Motor.REGIME_REGULAR, versao_app, versao_db),
        simulado=False,
        itens=(),
        totais=Totais(),
        payload_enviado={},
        resposta_bruta={},
    )


@pytest.mark.django_db
def test_versao_do_calculo_completa_data_da_base(motores_falsos):
    versao = registro.versao_do_calculo(_resultado())
    assert (versao.motor, versao.versao_app, versao.versao_db) == ("regime_regular", "1.7.2", "V0073")
    assert versao.data_db.isoformat() == "2026-11-09"
    assert registro.versao_do_calculo(_resultado()).pk == versao.pk  # não duplica
    assert VersaoMotor.objects.count() == 1


@pytest.mark.django_db
def test_versao_nova_no_header_vira_registro_novo(motores_falsos):
    registro.versao_do_calculo(_resultado())
    nova = registro.versao_do_calculo(_resultado("1.7.3", "V0074"))
    # /versao ainda diz 1.7.2: não mistura a data da base antiga com a versão nova.
    assert (nova.versao_app, nova.versao_db, nova.data_db) == ("1.7.3", "V0074", None)
    assert VersaoMotor.objects.count() == 2


@pytest.mark.django_db
def test_versao_do_calculo_funciona_com_versao_fora_do_ar(motores_falsos):
    motores_falsos.rotas.pop(("rr", "GET", "/api/calculadora/dados-abertos/versao"))
    versao = registro.versao_do_calculo(_resultado())
    assert versao.versao_app == "1.7.2" and versao.data_db is None


@pytest.mark.django_db
def test_registrar_versoes_atuais(motores_falsos):
    motores_falsos.fora_do_ar.add("df")
    versoes = registro.registrar_versoes_atuais()
    assert str(versoes[Motor.REGIME_REGULAR]) == "regime_regular 1.7.2 · base V0073"
    assert str(versoes[Motor.SIMPLES]) == "simples 1.0.2 · base V0073"
    assert versoes[Motor.DANFSE] is None


@pytest.mark.django_db
def test_painel_de_versoes(motores_falsos, criar_escritorio, cliente_de):
    from engines import versoes

    versoes.httpx.Client  # noqa: B018 - o painel usa o mesmo leitor do /healthz
    registro.registrar_versoes_atuais()
    _, _, dono = criar_escritorio()
    resposta = cliente_de(dono).get("/api/motores/versoes/")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert {v["motor"] for v in corpo["historico"]} == {"regime_regular", "simples", "danfse"}
    assert set(corpo["atuais"]) == {"regime_regular", "simples", "danfse"}
    assert corpo["ultima_sincronizacao_tabelas"] is None


def test_painel_exige_login(client):
    assert client.get("/api/motores/versoes/").status_code == 403
